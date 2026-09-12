// The backend repeats all access and lifecycle checks before any cleanup.
export function resetBlockers(state, validatedTarget, readiness, busy=false) {
  const reasons=[];
  if(!state.destructive_enabled) reasons.push("Cleanup is disabled in this release.");
  if(!state.is_admin) reasons.push("Add your HA user ID to this app’s admin_user_ids configuration, then restart the manager. Your ID is shown under Cleanup access in the connection section.");
  if(state.recovery_required || state.worker_active) return [...reasons,"Wait for the active job, or recover the interrupted job below."];
  if(busy) reasons.push("Wait for the current operation to finish.");
  if(!validatedTarget || !readiness) reasons.push("Validate the selected Frigate connection before cleanup.");
  else reasons.push(...readiness.blockers);
  return reasons;
}
export function cleanupBlockers(state, validatedTarget, readiness, approved, previewBusy=false, now=Date.now()) {
  const reasons=resetBlockers(state,validatedTarget,readiness,previewBusy);
  if(approved && approved.target!==validatedTarget) reasons.push("Validate the selected Frigate connection before cleanup.");
  if(!approved || approved.expired || (approved.expires_at && approved.expires_at*1000<=now)) reasons.push("Create a fresh preview of the history you want to remove.");
  else if(!Object.values(approved.result.counts).some(n=>n>0)) reasons.push("This preview has no eligible history to delete.");
  return reasons;
}

export const jobPhases={reserved:"Checking write and rename access",stopping:"Stopping Frigate",stopped:"Rechecking the approved selection",backup:"Backing up the database on NFS",prepared:"Backup verified",staging:"Staging media or restoring an uncommitted job",committing:"Updating the database",committed:"Database committed",purging:"Removing staged media",restarting:"Restoring Frigate’s original running state",completed:"Cleanup complete",rolled_back:"Cleanup rolled back — no selected history deleted",needs_preview:"Selection changed — create a fresh preview",rejected:"Cleanup did not start"};
export const terminalJobs=new Set(["completed","rolled_back","needs_preview","rejected"]);
Object.assign(jobPhases,{reset_backup:"Backing up the database for reset",reset_staging:"Staging all Frigate media",reset_checking:"Checking staged media before reset",reset_ready:"Media and database staged; committing reset",reset_committed:"Reset committed",reset_purging:"Deleting all staged Frigate media and database files",reset_restoring:"Restoring media and database before reset commit"});

export function confirmationText(approved, scope, bytes) {
  const counts=approved.result.counts;
  return `Delete this history from ${approved.target}?\n${scope}\nEstimated space: ${bytes(approved.result.bytes)}\n${counts.recordings||0} recording segments · ${counts.event||0} events · ${counts.reviewsegment||0} review items\n\nFrigate will pause. The manager checks access, backs up metadata and stages media before committing cleanup. Deleted video cannot be restored from the metadata backup.`;
}
