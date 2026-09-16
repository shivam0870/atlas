import http from 'k6/http';
import {check} from 'k6';
import exec from 'k6/execution';
export const options={scenarios:{ingestion:{executor:'shared-iterations',vus:2,iterations:40,maxDuration:'90s'}},thresholds:{checks:['rate==1'],http_req_failed:['rate==0']},summaryTrendStats:['avg','med','p(95)','p(99)','min','max']};
export default function(){
 const index=exec.scenario.iterationInTest;
 const title='Ingestion benchmark '+__ENV.BENCH_RUN+' '+index;
 const content=('Synthetic throughput fixture '+index+'. The service follows a reviewed release process. Its operators verify readiness, record the image digest, and monitor request latency. ').repeat(8);
 const response=http.post(__ENV.ATLAS_BASE+'/api/documents/text',JSON.stringify({title,content}),{headers:{Authorization:'Bearer '+__ENV.ATLAS_BENCH_KEY,'Content-Type':'application/json'}});
 check(response,{'durably accepted':r=>r.status===202&&Boolean(r.json('job_id'))});
}
export function handleSummary(data){return {[__ENV.BENCH_OUTPUT]:JSON.stringify(data,null,2)}}
