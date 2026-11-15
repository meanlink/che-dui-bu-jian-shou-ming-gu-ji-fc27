/** @typedef {{id:number,name:string,source:string,rows:number,checksum:string}} Dataset */
/** @typedef {{id:number,name:string,dataset_id:number,status:string,result:Object,approved:boolean}} AnalysisJob */
/** @typedef {{id:number,name:string,job_id:number,destination:string,next_run:number,enabled:boolean}} ReportSchedule */
/** @typedef {{id:number,plate_no:string,name:string,current_reading:number}} Vehicle */
/**
 * @typedef {Object} ComponentLedger
 * @property {number} id
 * @property {string} asset_no
 * @property {string} model
 * @property {'in_service'|'spare'|'failed'} status
 * @property {number} accumulated_km
 * @property {?string} plate_no
 * @property {?number} installed_reading
 * @property {?number} failed_km
 */
/**
 * @typedef {Object} HandoverEvent
 * @property {'remove'|'install'} action
 * @property {string} plate_no
 * @property {?number} reading
 * @property {?string} asset_no
 * @property {?string} model
 * @property {?('spare'|'failed')} outcome
 */
/**
 * @typedef {Object} CutoffSample
 * @property {number} cutoff_at
 * @property {Array<{asset:string,model:string,km:number,failed:0|1}>} records
 * @property {Array<Object>} open_segments
 * @property {{total:number,failed:number,censored:number,in_service:number,excluded:number}} counts
 * @property {string} checksum
 * @property {?number} dataset_id
 */
export {};
