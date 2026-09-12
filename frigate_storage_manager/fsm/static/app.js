"use strict";
import { relativeCutoff } from "./time.mjs";
import { createPreviewUI } from "./previews.mjs";
import { renderOverview } from "./overview.mjs";
const $ = (id) => document.getElementById(id);
let state = {}, approved = null, validatedTarget = null;
let previewUI = null;
const labels = {event:"Events",reviewsegment:"Reviews",recordings:"Recording segments",previews:"Preview videos",export:"Completed exports",timeline:"Timeline entries",userreviewstatus:"Review status records",snapshots:"Snapshot files",event_thumbnails:"Event thumbnail files",review_thumbnails:"Review thumbnail files",exports:"Export video files",export_thumbnails:"Export thumbnail files",vec_thumbnails:"Thumbnail vectors",vec_descriptions:"Description vectors",bookmarks:"Bookmarked events",active_events:"Unfinished events",active_reviews:"Unfinished reviews"};
const bytes = (value) => { let n=Number(value), u=0; const units=["B","KiB","MiB","GiB","TiB"]; while(n>=1024 && u<4){n/=1024;u++;} return `${n.toLocaleString(undefined,{maximumFractionDigits:1})} ${units[u]}`; };
function error(e){$("error").textContent=e.message;$("error").hidden=false;}
async function api(path, body){
  const response=await fetch(`api/${path}`,body === undefined ? {cache:"no-store"} : {method:"POST",headers:{"Content-Type":"application/json","X-FSM-CSRF":state.csrf},body:JSON.stringify(body)});
  const data=await response.json(); if(!response.ok){const failure=new Error(data.error || "Request failed");failure.diagnostics=data.diagnostics;throw failure;} return data;
}
function invalidate(){approved=null;$("result-section").hidden=true;$("delete").disabled=true;previewUI?.invalidate();}
function syncControls(){
  const busy=previewUI?.busy() || false;
  for(const id of ["target","cameras","method","exports","validate"])$(id).disabled=busy;
  for(const method of ["days","hours","date"])$(method).disabled=busy||$("method").value!==method;
  $("preview").disabled=busy||!validatedTarget||state.recovery_required;
  $("preview").textContent=busy?"Preview in progress…":"Preview cleanup";
  $("probe").disabled=busy||!state.is_admin||!validatedTarget;
}
function cutoff(){
  const method=$("method").value;
  if(method==="days" || method==="hours"){
    return relativeCutoff(method,$(method).value);
  }
  const input=$("date").value, value=new Date(input);
  if(!input || Number.isNaN(value.getTime()))throw new Error("Choose a local date and time");
  // Reject nonexistent DST local times instead of silently shifting the cutoff.
  const local=new Date(value.getTime()-value.getTimezoneOffset()*60000).toISOString().slice(0,16);
  if(local!==input)throw new Error("That local time does not exist because of a daylight-saving transition");
  return value;
}
function boundary(){
  try{const value=cutoff();$("boundary").textContent=`Timezone: ${Intl.DateTimeFormat().resolvedOptions().timeZone}. Before ${value.toLocaleString()} (${value.toISOString()}). A day means 24 hours; repeated DST times use the first occurrence.`;}
  catch(e){$("boundary").textContent=e.message;}
}
function list(id, values){$(id).replaceChildren();for(const [key,value] of Object.entries(values)){if(!value)continue;const dt=document.createElement("dt"),dd=document.createElement("dd");dt.textContent=labels[key]||key;dd.textContent=Number(value).toLocaleString();$(id).append(dt,dd);}}
async function refreshStatus(){
  state=await api("status");$("user").textContent=state.user_id;$("probe").disabled=!state.is_admin||!validatedTarget;
  const jobs=$("jobs");jobs.replaceChildren();if(!state.jobs.length)jobs.textContent="No cleanup jobs.";
  for(const job of state.jobs){const box=document.createElement("div");box.className="job";const title=document.createElement("strong");title.textContent=`${job.target} · ${job.phase}`;box.append(title);
    const text=document.createElement("p");text.textContent=job.recovery_error||job.error||`Media removed: ${bytes(job.media_bytes_removed||0)}${job.processed ? ` · Staged files: ${job.processed}` : ""}`;box.append(text);
    if(!["completed","rolled_back","needs_preview","rejected"].includes(job.phase)){const button=document.createElement("button");button.textContent="Recover interrupted job";button.disabled=!state.destructive_enabled||!state.is_admin;button.onclick=()=>act(async()=>{if(confirm(`Recover ${job.target}? The journal will decide whether to restore staging or finish committed deletion.`)){await api("recover",{job_id:job.id});await refreshStatus();}});box.append(button);}
    if(job.backup&&!job.backup_removed){const b=document.createElement("button");b.textContent="Remove completed metadata backup";b.disabled=!state.destructive_enabled||!state.is_admin||state.recovery_required;b.onclick=()=>act(async()=>{if(confirm("Remove this completed metadata backup and journal? Deleted video cannot be restored by metadata.")){await api("backups/remove",{job_id:job.id});await refreshStatus();}});box.append(b);}jobs.append(box);
  }
  if(state.recovery_required)$("preview").disabled=true;
  await previewUI?.acceptStatus(state);
  syncControls();
}
async function act(fn){$("error").hidden=true;try{await fn();}catch(e){error(e);}}
$("validate").onclick=()=>act(async()=>{
  invalidate();validatedTarget=null;$("preview").disabled=true;$("probe").disabled=true;$("validate").disabled=true;$("diagnostics").textContent="Validation in progress…";$("connection").textContent="Validating selected database, schema, NFS and Supervisor access…";
  try{const data=await api("validate",{target:$("target").value});validatedTarget=data.target;$("connection").textContent=`${data.target} · Frigate ${data.version} · ${data.state} · NFS available`;
    for(const key of ["used","free","total"])$(key).textContent=bytes(data.media[key]);$("storage").hidden=false;$("diagnostics").textContent=JSON.stringify(data,null,2);
    $("cameras").replaceChildren();for(const name of data.cameras){const option=document.createElement("option");option.value=name;option.textContent=name+(data.historical_cameras.includes(name)?" (historical)":"");option.selected=true;$("cameras").append(option);}$("preview").disabled=state.recovery_required;$("probe").disabled=!state.is_admin;
  }catch(e){$("storage").hidden=true;$("connection").textContent="Validation blocked. See the details below.";$("diagnostics").textContent=JSON.stringify({error:e.message,...(e.diagnostics||{})},null,2);$("validation-details").open=true;throw e;}finally{syncControls();}
});
$("probe").onclick=()=>act(async()=>{const result=await api("probe",{target:validatedTarget});$("diagnostics").textContent+="\n"+JSON.stringify(result,null,2);});
$("target").onchange=()=>{validatedTarget=null;$("preview").disabled=true;$("probe").disabled=true;invalidate();};
$("method").onchange=()=>{for(const method of ["days","hours","date"]){const active=$("method").value===method;$(method+"-label").hidden=!active;$(method).required=active;$(method).disabled=!active;}invalidate();boundary();};
for(const id of ["days","hours","date","cameras","exports"])$(id).addEventListener("input",()=>{invalidate();boundary();});
$("preview-form").onsubmit=(event)=>{event.preventDefault();act(async()=>{
  await previewUI.start({target:validatedTarget,cameras:[...$("cameras").selectedOptions].map(o=>o.value),cutoff:cutoff().toISOString(),include_exports:$("exports").checked});
});};
$("delete").onclick=()=>act(async()=>{if(!approved)return;const counts=Object.entries(approved.result.counts).map(([k,v])=>`${labels[k]||k}: ${v}`).join("\n");if(confirm(`Delete selected history?\n${$("scope").textContent}\n${counts}\nEstimated media: ${bytes(approved.result.bytes)}\nFrigate pauses during cleanup. Deleted video cannot be restored from a metadata backup.`)){
  await api("delete",{preview_id:approved.preview_id,confirmation:approved.confirmation});invalidate();await refreshStatus();}});
previewUI=createPreviewUI({api,bytes,labels,onBusy:syncControls,onError:error,invalidate,
  onRequest:(request)=>{
    if(validatedTarget!==request.target)validatedTarget=null;
    $("target").value=request.target;
    if(!validatedTarget)$("cameras").replaceChildren();
    for(const camera of request.cameras){if(![...$("cameras").options].some(o=>o.value===camera)){const option=document.createElement("option");option.value=camera;option.textContent=camera;$("cameras").append(option);}}
    for(const option of $("cameras").options)option.selected=request.cameras.includes(option.value);
    $("method").value="date";const time=new Date(request.cutoff);$("date").value=new Date(time.getTime()-time.getTimezoneOffset()*60000).toISOString().slice(0,16);$("exports").checked=request.include_exports;
    for(const method of ["days","hours","date"]){$(method+"-label").hidden=method!=="date";$(method).required=method==="date";}boundary();syncControls();
  },
  onReady:(task)=>{
    approved=task.expired?null:{...task,target:task.request.target};const r=task.result;
    $("scope").textContent=`${r.scope.cameras.join(", ")} · Footage ending before ${new Date(r.scope.cutoff_utc).toLocaleString(undefined,{dateStyle:"medium",timeStyle:"medium"})} · Exports ${r.scope.include_exports?"included":"kept"}`;
    $("recoverable").textContent=bytes(r.bytes);list("counts",r.counts);list("preserved",r.preserved);$("result-section").hidden=false;$("delete").disabled=task.expired||!state.destructive_enabled||!state.is_admin;
    renderOverview(task,{api,bytes});
  }
});
act(async()=>{await refreshStatus();const data=await api("discovery");for(const item of data.apps){const option=document.createElement("option");option.value=item.slug;option.textContent=`${item.name} · ${item.slug} · ${item.version}`;$("target").append(option);}$("target").value=data.configured_target;$("connection").textContent=data.apps.length?"Select Frigate, then validate its storage.":"No installed Frigate app found.";await previewUI.acceptStatus(state,true);boundary();});
// Only poll small local job records while a job is unresolved; never scan media.
setInterval(()=>{if(state.recovery_required)act(refreshStatus);},5000);
