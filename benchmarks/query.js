import http from 'k6/http';
import {check} from 'k6';
import {Rate} from 'k6/metrics';
const cacheHits=new Rate('answer_cache_hit');
const failures=new Rate('answer_failure');
const cached=__ENV.BENCH_MODE==='cached';
export const options={
 scenarios: cached?{cached:{executor:'constant-arrival-rate',rate:20,timeUnit:'1s',duration:'30s',preAllocatedVUs:4,maxVUs:8}}:{uncached:{executor:'constant-vus',vus:1,duration:'30s',gracefulStop:'120s'}},
 thresholds:{answer_failure:['rate==0'],http_req_failed:['rate==0']},
 summaryTrendStats:['avg','med','p(95)','p(99)','min','max'],
};
export default function(){
 const response=http.post(__ENV.ATLAS_BASE+'/api/query',JSON.stringify({question:'What is the checkout release approval code?',top_k:3,use_cache:cached}),{headers:{Authorization:'Bearer '+__ENV.ATLAS_BENCH_KEY,'Content-Type':'application/json'},timeout:'125s'});
 const done=String(response.body).split('\n\n').find(frame=>frame.startsWith('event: done'));
 let result={};try{result=JSON.parse(done.split('\ndata: ')[1])}catch{}
 const ok=check(response,{'HTTP 200':r=>r.status===200,'completed grounded answer':()=>result.status==='completed'&&String(response.body).includes('AMBER-ORCHID')});
 failures.add(!ok);cacheHits.add(result.cache_hit===true);
}
export function handleSummary(data){return {[__ENV.BENCH_OUTPUT]:JSON.stringify(data,null,2)}}
