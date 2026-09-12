const $ = (id) => document.getElementById(id);
const counted = (count, name) => `${count.toLocaleString()} ${name}${count===1 ? "" : "s"}`;

export function duration(seconds) {
  const minutes = Math.floor(seconds / 60), hours = Math.floor(minutes / 60);
  if (hours) return `${hours.toLocaleString()} hr ${minutes % 60} min`;
  if (minutes) return `${minutes} min`;
  return `${Math.floor(seconds)} sec`;
}

export function decisions(result) {
  const count = (key) => result.counts[key] || 0;
  const kept = (key) => result.preserved[key] || 0;
  const descriptions = [];
  if (count("event")) descriptions.push(counted(count("event"),"event"));
  if (count("reviewsegment")) descriptions.push(counted(count("reviewsegment"),"review item"));
  const removal = descriptions.length ? `${descriptions.join(" and ")} would also be removed with their associated history.` : "No events or review items would be removed.";
  const bookmarks = kept("bookmarks") ? `${counted(kept("bookmarks"),"bookmarked event")} ${kept("bookmarks")===1 ? "stays" : "stay"}, together with linked history and required footage.` : "No bookmarked events were found for these cameras. This selection does not demonstrate bookmark protection.";
  return { removal, bookmarks };
}

const reasons = {
  recent: "History ending at or after the cutoff stays, including footage crossing that time.",
  required_footage: "Older footage needed by kept history or unfinished exports stays.",
  linked_history: "Older events/reviews needed by linked history, triggers or unfinished exports stay.",
  unfinished: "Unfinished history and exports stay.",
  exports: "Completed exports outside the chosen export policy stay.",
};
function range(start,end) {
  const from=new Date(start*1000),to=new Date(end*1000),timeStyle=end-start<60 ? "medium" : "short";
  return `${from.toLocaleString(undefined,{dateStyle:"medium",timeStyle})} – ${from.toDateString()===to.toDateString() ? to.toLocaleTimeString(undefined,{timeStyle}) : to.toLocaleString(undefined,{dateStyle:"medium",timeStyle})}`;
}
function element(tag, text, className) { const node=document.createElement(tag); if(text)node.textContent=text; if(className)node.className=className; return node; }

export function renderOverview(task, { api, bytes }) {
  const result=task.result, root=$("camera-overview"); root.replaceChildren();
  const description=decisions(result);
  $("decision-summary").textContent=description.removal;
  $("bookmark-summary").textContent=description.bookmarks;
  $("recording-duration").textContent=result.overview ? `${duration(result.overview.recording_seconds)} of camera recordings` : "Create a fresh preview for a grouped footage summary.";
  for(const camera of result.overview?.cameras || []) {
    const box=element("article",null,"camera-card");
    box.append(element("h3",camera.camera));
    const columns=element("div",null,"columns"), remove=element("div"), keep=element("div");
    remove.append(element("h4","Would remove"),element("strong",bytes(camera.bytes),"camera-size"));
    remove.append(element("p",camera.selected.recordings ? `${duration(camera.recording_seconds)} of recordings within ${range(camera.start,camera.end)}.` : "No recordings selected."));
    const extra=[];
    for(const [key,label] of [["previews","preview video"],["event","event"],["reviewsegment","review item"],["export","completed export"]]) if(camera.selected[key])extra.push(counted(camera.selected[key],label));
    if(extra.length)remove.append(element("p",extra.join(" · "),"hint"));
    keep.append(element("h4","Would keep"));
    const events=camera.kept.event||0,reviews=camera.kept.reviewsegment||0;
    const all=!(camera.selected.event||camera.selected.reviewsegment);
    const keptLabels=[];if(events)keptLabels.push(counted(events,"event"));if(reviews)keptLabels.push(counted(reviews,"review item"));
    keep.append(element("p",keptLabels.length ? `${all&&events+reviews>1 ? "All " : ""}${keptLabels.join(" and ")} ${events+reviews===1 ? "stays" : "stay"}.` : "No events or review items in the kept selection."));
    for(const [key,message] of Object.entries(reasons))if(camera.reasons[key])keep.append(element("p",message,"hint"));
    columns.append(remove,keep);box.append(columns);
    if(camera.selected.recordings && !task.expired) {
      const details=element("details"), summary=element("summary","See recording time ranges");
      const content=element("div"); details.append(summary,content);box.append(details);
      let page=0;
      async function load() {
        content.replaceChildren(element("p","Loading time ranges…","hint"));
        try {
          const data=await api(`previews/${task.id}/hours?${new URLSearchParams({camera:camera.camera,page})}`);
          content.replaceChildren(element("p","Grouped by the hour recordings start. Ranges can contain gaps; footage duration excludes gaps and overlapping time. Camera totals count overlaps across all ranges once.","hint"));
          const wrapper=element("div",null,"table-scroll"),table=element("table"),head=element("tr");
          for(const text of ["Recorded between","Footage","Space"])head.append(element("th",text));
          const thead=element("thead");thead.append(head);table.append(thead);
          const body=element("tbody");
          for(const item of data.items){const row=element("tr");for(const text of [range(item.start,item.end),duration(item.recording_seconds),bytes(item.bytes)])row.append(element("td",text));body.append(row);}
          table.append(body);wrapper.append(table);content.append(wrapper);
          if(data.matched>12){const nav=element("div",null,"pagination");const previous=element("button","Earlier ranges","secondary"),next=element("button","Later ranges","secondary");previous.disabled=page===0;next.disabled=(page+1)*12>=data.matched;previous.onclick=()=>{page--;load();};next.onclick=()=>{page++;load();};nav.append(previous,element("span",`${page*12+1}–${Math.min((page+1)*12,data.matched)} of ${data.matched}`,"hint"),next);content.append(nav);}
        } catch(error){content.replaceChildren(element("p",error.message,"hint"));}
      }
      details.ontoggle=()=>{if(details.open&&!content.hasChildNodes())load();};
    }
    root.append(box);
  }
}
