"""Bounded, lease-based generation scheduling shared by every API/worker process."""

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import HTTPException

from atlas.config import settings
from atlas.db import access_context

# Every mutation runs in one Redis script. Waiting requests have expiring leases;
# crashed clients cannot occupy capacity indefinitely. Last-served tenant order
# gives other tenants a turn even when one tenant has many queued requests.
SCHEDULE = """
local t=redis.call('TIME')
local now=tonumber(t[1])+tonumber(t[2])/1000000
local token,tenant=ARGV[1],ARGV[2]
for _,key in ipairs({KEYS[1],KEYS[3]}) do
  local expired=redis.call('ZRANGEBYSCORE',key,'-inf',now)
  for _,item in ipairs(expired) do
    redis.call('ZREM',key,item)
    redis.call('HDEL',key..':tenants',item)
    redis.call('HDEL',key..':limits',item)
  end
end
if ARGV[3]=='release' then
  for _,key in ipairs({KEYS[1],KEYS[3]}) do
    redis.call('ZREM',key,token)
    redis.call('HDEL',key..':tenants',token)
    redis.call('HDEL',key..':limits',token)
  end
  return 0
end
if not redis.call('ZSCORE',KEYS[1],token) then
  if ARGV[3]~='join' then return -3 end
  if redis.call('ZCARD',KEYS[1])>=tonumber(ARGV[4]) then return -1 end
  local count=0
  for _,value in ipairs(redis.call('HVALS',KEYS[1]..':tenants')) do
    if value==tenant then count=count+1 end
  end
  if count>=tonumber(ARGV[5]) then return -2 end
  redis.call('ZADD',KEYS[1],now+tonumber(ARGV[6]),token)
  redis.call('HSET',KEYS[1]..':tenants',token,tenant)
  redis.call('HSET',KEYS[1]..':limits',token,ARGV[8])
end
if redis.call('ZCARD',KEYS[3])>=tonumber(ARGV[7]) then return 0 end
local active={}
for _,value in ipairs(redis.call('HVALS',KEYS[3]..':tenants')) do
  active[value]=(active[value] or 0)+1
end
local candidate=nil
local best=math.huge
for _,item in ipairs(redis.call('ZRANGE',KEYS[1],0,-1)) do
  local owner=redis.call('HGET',KEYS[1]..':tenants',item)
  local capacity=tonumber(redis.call('HGET',KEYS[1]..':limits',item) or '1')
  local served=tonumber(redis.call('HGET',KEYS[2],owner) or '0')
  if (active[owner] or 0)<capacity and served<best then
    candidate=item
    best=served
  end
end
if candidate~=token then return 0 end
redis.call('ZREM',KEYS[1],token)
redis.call('HDEL',KEYS[1]..':tenants',token)
redis.call('HDEL',KEYS[1]..':limits',token)
redis.call('ZADD',KEYS[3],now+tonumber(ARGV[9]),token)
redis.call('HSET',KEYS[3]..':tenants',token,tenant)
redis.call('HSET',KEYS[2],tenant,now)
redis.call('EXPIRE',KEYS[2],86400)
return 1
"""


@asynccontextmanager
async def generation_slot():
    from atlas.serving import limits, redis

    actor = access_context.get()
    tenant = str(actor.tenant_id) if actor else "system"
    quota = await limits(actor.tenant_id) if actor else {}
    token = str(uuid4())
    keys = ("atlas:generation:waiting", "atlas:generation:turns", "atlas:generation:active")

    async def command(action):
        return await redis.eval(
            SCHEDULE,
            3,
            *keys,
            token,
            tenant,
            action,
            settings.generation_queue_size,
            quota.get("queued_generations", 8),
            settings.generation_queue_timeout,
            settings.generation_slots,
            quota.get("concurrent_generations", 1),
            settings.model_timeout + 30,
        )

    try:
        async with asyncio.timeout(settings.generation_queue_timeout):
            action = "join"
            while True:
                try:
                    admitted = await command(action)
                except Exception as exc:
                    raise HTTPException(503, "Generation scheduler is unavailable") from exc
                if admitted == 1:
                    break
                if admitted < 0:
                    raise HTTPException(
                        429,
                        "Generation queue is full; source search remains available",
                        headers={"Retry-After": "5"},
                    )
                action = "poll"
                await asyncio.sleep(0.1)
        yield
    finally:
        # Shield only the bounded release operation; capacity also expires after a crash.
        try:
            await asyncio.wait_for(asyncio.shield(command("release")), timeout=2)
        except Exception:
            pass
