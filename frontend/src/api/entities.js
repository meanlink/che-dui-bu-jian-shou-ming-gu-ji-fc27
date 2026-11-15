import {api} from './client.js';
export const datasets={list:()=>api('/datasets'),get:id=>api('/datasets/'+id),create:data=>api('/datasets',{method:'POST',body:JSON.stringify(data)})};
export const jobs={list:()=>api('/jobs'),get:id=>api('/jobs/'+id),create:data=>api('/jobs',{method:'POST',body:JSON.stringify(data)}),approve:id=>api('/jobs/'+id+'/approve',{method:'POST'})};
export const reports={list:()=>api('/reports'),create:data=>api('/reports',{method:'POST',body:JSON.stringify(data)}),toggle:id=>api('/reports/'+id+'/toggle',{method:'POST'}),deliveries:()=>api('/deliveries')};
export const fleet={
 vehicles:()=>api('/fleet/vehicles'),
 components:()=>api('/fleet/components'),
 handovers:()=>api('/fleet/handovers'),
 registerVehicle:data=>api('/fleet/vehicles',{method:'POST',body:JSON.stringify(data)}),
 registerComponent:data=>api('/fleet/components',{method:'POST',body:JSON.stringify(data)}),
 submitHandover:data=>api('/fleet/handovers',{method:'POST',body:JSON.stringify(data)}),
 history:q=>api('/fleet/history'+(q?'?'+new URLSearchParams(q).toString():'')),
 previewSample:data=>api('/fleet/samples/preview',{method:'POST',body:JSON.stringify(data)}),
 createSample:data=>api('/fleet/samples',{method:'POST',body:JSON.stringify(data)}),
};
