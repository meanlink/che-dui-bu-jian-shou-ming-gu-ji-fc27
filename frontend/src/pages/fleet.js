import {fleet} from '../api/entities.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {table} from '../components/common/table.js';
import {esc,fmt,date,notify} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';

const canWrite=()=>session.user.role!=='viewer';
const STATUS={in_service:['在车上','ok'],spare:['卸下待用','muted'],failed:['已失效','failed']};

function statusTag(s){const [label,cls]=STATUS[s]||[s,''];return '<span class="status '+cls+'">'+esc(label)+'</span>';}

export async function fleetPage(){
 const [vehicles,components,handovers]=await Promise.all([fleet.vehicles(),fleet.components(),fleet.handovers()]);
 const mountedVehicles=vehicles.filter(v=>components.some(c=>c.plate_no===v.plate_no));
 const spareComponents=components.filter(c=>c.status!=='failed'&&!c.plate_no);
 return heading('06 / SWAP LEDGER','换装履历与部件里程','车表读数属于车辆；部件里程按每段「卸车读数−上车读数」逐段累加。交接整单生效，任一冲突整单不动；同单号重试不重复累计。')
 // 资产登记 + 当前占位
 +'<div class="dataset-grid"><section class="panel"><div class="section-title"><h2>车辆与当前车表</h2><span>'+vehicles.length+' 辆</span></div>'
 +table([{key:'plate_no',label:'车牌'},{key:'name',label:'名称'},{key:'current_reading',label:'当前车表读数'}],vehicles)
 +'<details class="register-box" '+(canWrite()?'':'hidden')+'><summary>登记新车辆 / 新部件</summary>'
 +'<form id="asset-vehicle"><label>车牌号<input name="plate_no" required placeholder="闽D-B003"></label><label>名称<input name="name" placeholder="线路与车号"></label><label>初始车表读数<input name="initial_reading" type="number" step="any" min="0" value="0" required></label><button class="small primary">登记车辆</button></form>'
 +'<form id="asset-component"><label>部件资产号<input name="asset_no" required placeholder="AC8-A003"></label><label>型号<input name="model" required placeholder="AC-8"></label><button class="small primary">登记部件</button></form></details></section>'
 +'<section class="panel"><div class="section-title"><h2>部件里程台账</h2><span>累计里程非车表读数</span></div>'
 +table([{key:'asset_no',label:'资产号'},{key:'model',label:'型号'},{key:'status',label:'状态',render:statusTag},{key:'accumulated_km',label:'累计里程'},{key:'plate_no',label:'所在车辆'},{key:'installed_reading',label:'上车读数'}],components)
 +'</section></div>'
 // 交接登记
 +'<section class="panel"><div class="section-title"><h2>登记换装交接</h2><span>支持单车换装与两车成对调换</span></div>'
 +'<form id="handover-form" class="handover-form"><div class="parameter-grid"><label>交接单号（幂等键）<input name="handover_no" required minlength="2" placeholder="HO-202611-009"></label><label>备注<input name="note" placeholder="例如：到检换件 / 成对调换"></label></div>'
 +'<div id="event-rows"></div><div class="event-toolbar" '+(canWrite()?'':'hidden')+'><button type="button" class="small" id="add-remove">+ 卸车段</button><button type="button" class="small" id="add-install">+ 上车段</button><button type="button" class="small" id="clear-events">清空</button></div>'
 +'<p class="hint">卸车段需填车表读数并选择「卸下待用 / 失效」；上车段读数不得低于车辆当前车表，迟到交接会被拒绝。成对调换须两辆车的卸、装集合一致。</p>'
 +'<button type="submit" class="primary" '+(canWrite()?'':'disabled')+'>整单提交交接 ↗</button></form></section>'
 // 履历
 +'<section class="panel"><div class="section-title"><h2>换装履历</h2><span>按资产号或车牌过滤</span></div>'
 +'<form id="history-filter" class="inline-filter"><input name="asset_no" placeholder="部件资产号，如 AC8-A002"><input name="plate_no" placeholder="车牌，如 闽D-B001"><button class="small">查询</button><a class="text-button" href="#/fleet">重置</a></form>'
 +'<div id="history-table">'+historyTable(handovers)+'</div></section>'
 // 截止样本
 +'<section class="panel"><div class="section-title"><h2>按截止时刻生成估计样本</h2><span>与既有 Weibull 输入同构</span></div>'
 +'<form id="sample-form" class="sample-form"><label>截止时刻（本地时间）<input name="cutoff_text" type="datetime-local" step="1" required></label>'
 +'<div class="cutoff-readings"><p class="hint">尚在车上的部件需补足所在车辆的截止车表读数；缺任一读数整批阻断。</p>'
 +(mountedVehicles.length?mountedVehicles.map(v=>'<label>'+esc(v.plate_no)+' 截止读数（当前 '+fmt(v.current_reading)+'）<input name="reading-'+esc(v.plate_no)+'" type="number" step="any" min="0" placeholder="截止车表"></label>').join(''):'<p class="empty">当前没有在车上的部件</p>')
 +'</div><label '+(canWrite()?'':'hidden')+'>存为数据批次名称（留空仅预览）<input name="name" placeholder="换装截止样本-202611"></label>'
 +'<div class="event-toolbar"><button type="button" class="small" id="preview-sample">预览样本</button>'
 +'<button type="submit" class="small primary" '+(canWrite()?'':'disabled')+'>生成并接入批次</button></div></form>'
 +'<div id="sample-result"></div></section>';
}

function historyTable(handovers){
 const rows=handovers.flatMap(o=>o.events.map((e,i)=>({
  handover_no:o.handover_no+(i===0?'':''),action:e.action==='remove'?'卸车':'上车',
  plate_no:e.payload?.plate_no??'',asset_no:e.payload?.asset_no??'',
  reading:e.reading,segment_km:e.segment_km,
  outcome:e.outcome==='failed'?'失效':e.outcome==='spare'?'待用':'',at:o.created_at,
 })));
 return table([{key:'handover_no',label:'交接单号'},{key:'action',label:'动作'},{key:'plate_no',label:'车牌'},{key:'asset_no',label:'部件'},{key:'reading',label:'车表读数'},{key:'segment_km',label:'本段里程'},{key:'outcome',label:'卸下结论'},{key:'at',label:'时间',render:date}],rows,'暂无交接记录。');
}

function eventRow(kind,vehicles,components){
 const vopts=vehicles.map(v=>'<option value="'+esc(v.plate_no)+'">').join('');
 const isRemove=kind==='remove';
 return '<div class="event-row"><strong>'+(isRemove?'卸车段':'上车段')+'</strong>'
 +'<label>车辆<select name="plate">'+vopts+'</select></label>'
 +'<label>部件<select name="asset">'+components.map(c=>'<option value="'+esc(c.asset_no)+'">'+esc(c.asset_no)+' · '+esc(c.model)+'</option>').join('')+'</select></label>'
 +'<label>车表读数<input name="reading" type="number" step="any" min="0" required></label>'
 +(isRemove?'<label>卸下结论<select name="outcome"><option value="spare">卸下待用</option><option value="failed">失效</option></select></label>':'')
 +'<button type="button" class="text-button drop-event">移除</button></div>';
}

export function bindFleet(refresh){
 const load=()=>Promise.all([fleet.vehicles(),fleet.components()]);
 // 资产登记
 const vf=document.querySelector('#asset-vehicle');
 if(vf)submit(vf,async f=>{await fleet.registerVehicle({plate_no:f.get('plate_no'),name:f.get('name'),initial_reading:Number(f.get('initial_reading'))});notify('车辆已登记');await refresh();});
 const cf=document.querySelector('#asset-component');
 if(cf)submit(cf,async f=>{await fleet.registerComponent({asset_no:f.get('asset_no'),model:f.get('model')});notify('部件已登记');await refresh();});
 // 交接事件行
 const box=document.querySelector('#event-rows');
 async function addRow(kind){const [vehicles,components]=await load();box.insertAdjacentHTML('beforeend',eventRow(kind,vehicles,components));}
 document.querySelector('#add-remove').onclick=()=>addRow('remove');
 document.querySelector('#add-install').onclick=()=>addRow('install');
 document.querySelector('#clear-events').onclick=()=>{box.innerHTML='';};
 box.addEventListener('click',e=>{if(e.target.classList.contains('drop-event'))e.target.closest('.event-row').remove();});
 // 提交交接
 submit(document.querySelector('#handover-form'),async f=>{
  const rows=[...box.querySelectorAll('.event-row')];
  if(!rows.length)throw new Error('请至少添加一段卸车或上车动作');
  const events=rows.map(r=>({action:r.querySelector('strong').textContent==='卸车段'?'remove':'install',
   plate_no:r.querySelector('[name=plate]').value,asset_no:r.querySelector('[name=asset]').value,
   reading:Number(r.querySelector('[name=reading]').value),
   outcome:r.querySelector('[name=outcome]')?.value||null}));
  const res=await fleet.submitHandover({handover_no:f.get('handover_no'),note:f.get('note'),events});
  notify(res.created?'交接已整单生效':'相同交接已受理，重试未重复累计里程');
  await refresh();
 });
 // 履历过滤
 const hf=document.querySelector('#history-filter');
 hf.addEventListener('submit',async e=>{e.preventDefault();const q=Object.fromEntries(new FormData(hf));
  try{const rows=await fleet.history(Object.fromEntries(Object.entries(q).filter(([,v])=>v)));
   document.querySelector('#history-table').innerHTML=table([{key:'handover_no',label:'交接单号'},{key:'action',label:'动作'},{key:'plate_no',label:'车牌'},{key:'asset_no',label:'部件'},{key:'reading',label:'车表读数'},{key:'segment_km',label:'本段里程'},{key:'outcome',label:'卸下结论'},{key:'at',label:'时间',render:date}],rows,'该过滤条件下没有履历。');
  }catch(err){notify(err.message,true);}});
 // 截止样本
 async function collectSample(f){
  const cutoffText=f.get('cutoff_text');if(!cutoffText)throw new Error('请选择截止时刻');
  const cutoffAt=Math.floor(new Date(cutoffText).getTime()/1000);
  const readings={};for(const [k,v] of f.entries()){if(k.startsWith('reading-')&&v!=='')readings[k.slice(8)]=Number(v);}
  return {cutoff_at:cutoffAt,cutoff_readings:readings,name:f.get('name')||null};
 }
 function renderSample(b){
  const cols=[{key:'asset',label:'资产号'},{key:'model',label:'型号'},{key:'km',label:'观测里程'},{key:'failed',label:'失效1/在役0'}];
  document.querySelector('#sample-result').innerHTML='<div class="sample-summary"><span>样本 '+b.counts.total+' 件</span><span>失效 '+b.counts.failed+'</span><span>右删失 '+b.counts.censored+'</span><span>在车补差 '+b.counts.in_service+'</span><span>无暴露排除 '+b.counts.excluded+'</span></div>'
   +(b.excluded_no_exposure?.length?'<p class="warning">零里程库内件未纳入：'+b.excluded_no_exposure.map(x=>esc(x.asset_no)).join('、')+'</p>':'')
   +table(cols,b.records)+(b.dataset_id?'<p class="hint">已接入为数据批次 #'+b.dataset_id+'，可直接前往工作台拟合。</p>':'');
 }
 document.querySelector('#preview-sample').onclick=async()=>{try{const f=new FormData(document.querySelector('#sample-form'));renderSample(await fleet.previewSample(await collectSample(f)));}catch(e){notify(e.message,true);}};
 submit(document.querySelector('#sample-form'),async f=>{const b=await fleet.createSample(await collectSample(f));renderSample(b);notify('截止样本已接入数据批次');});
}
