// Draft service: public source availability only; never production/member data.
export const SCHEMA = `CREATE TABLE IF NOT EXISTS facility_snapshots (
  facility TEXT PRIMARY KEY,
  checked_at TEXT NOT NULL,
  success_at TEXT,
  result TEXT NOT NULL,
  payload TEXT
);`;
const FACILITIES = new Set(['hiranuma', 'tobe', 'karuizawa', 'katabira', 'kikuna']);
const STATUSES = new Set(['available_in_source', 'available_candidate', 'occupied',
  'unavailable', 'unknown', 'occupied_or_unknown']);
const MAX_BYTES = 256 * 1024;
const STALE_MS = 6 * 60 * 60 * 1000;
export const UPSERT = `INSERT INTO facility_snapshots
  (facility, checked_at, success_at, result, payload) VALUES (?, ?, ?, ?, ?)
  ON CONFLICT(facility) DO UPDATE SET checked_at=excluded.checked_at,
  result=excluded.result,
  success_at=COALESCE(excluded.success_at,facility_snapshots.success_at),
  payload=COALESCE(excluded.payload,facility_snapshots.payload)
  WHERE excluded.checked_at > facility_snapshots.checked_at`;

function json(data, status = 200, origin = null) {
  const headers = {'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'};
  if (origin) headers['Access-Control-Allow-Origin'] = origin;
  return new Response(JSON.stringify(data), {status, headers});
}
function onlyKeys(value, keys) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.keys(value).some(k => !keys.includes(k))) throw new Error('Unexpected fields');
}
export function validate(input, now = Date.now()) {
  onlyKeys(input, ['schema_version', 'facility', 'checked_at', 'result', 'cells']);
  if (input.schema_version !== 1 || !FACILITIES.has(input.facility) ||
      !['ok','error'].includes(input.result)) throw new Error('Invalid envelope');
  const when = Date.parse(input.checked_at);
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(input.checked_at) ||
      !Number.isFinite(when) || now-when > 24*60*60*1000 || when-now > 5*60*1000)
    throw new Error('Invalid check time');
  if (!Array.isArray(input.cells) || input.cells.length > 1500 ||
      (input.result === 'ok' && input.cells.length === 0) ||
      (input.result === 'error' && input.cells.length !== 0)) throw new Error('Invalid cell count');
  const seen = new Set();
  for (const cell of input.cells) {
    onlyKeys(cell, ['room', 'date', 'period', 'status']);
    if (typeof cell.room !== 'string' || !cell.room.trim() || cell.room.length > 80 ||
        !/^\d{4}-\d{2}-\d{2}$/.test(cell.date) ||
        !['午前','午後','夜間'].includes(cell.period) || !STATUSES.has(cell.status))
      throw new Error('Invalid cell');
    const date = new Date(cell.date+'T00:00:00Z');
    if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0,10) !== cell.date)
      throw new Error('Invalid calendar date');
    // Image blanks must never be promoted to confirmed availability.
    if (input.facility === 'katabira' && cell.status === 'available_in_source')
      throw new Error('Image availability needs review');
    const key = JSON.stringify([cell.room,cell.date,cell.period]);
    if (seen.has(key)) throw new Error('Duplicate cell');
    seen.add(key);
  }
  return input;
}
async function readLimited(request) {
  if (Number(request.headers.get('Content-Length')) > MAX_BYTES) throw new Error('Body too large');
  const reader = request.body?.getReader();
  if (!reader) throw new Error('Missing body');
  let total = 0; const parts = [];
  for (;;) {
    const {done,value} = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > MAX_BYTES) {await reader.cancel(); throw new Error('Body too large');}
    parts.push(value);
  }
  const bytes = new Uint8Array(total); let offset = 0;
  for (const part of parts) {bytes.set(part,offset); offset += part.byteLength;}
  return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));
}
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const origin = request.headers.get('Origin') === env.APP_ORIGIN ? env.APP_ORIGIN : null;
    if (url.pathname === '/health' && request.method === 'GET')
      return json({service:'rehearsal-availability',schema_version:1,stage:'draft'},200,origin);
    if (!env.DB) return json({error:'service_not_configured'},503,origin);
    if (url.pathname === '/ingest' && request.method === 'POST') {
      // No fallback token, no token in URLs, no logging of request bodies.
      if (typeof env.INGEST_TOKEN !== 'string' || env.INGEST_TOKEN.length < 32)
        return json({error:'ingest_not_configured'},503);
      if (request.headers.get('Authorization') !== 'Bearer '+env.INGEST_TOKEN)
        return json({error:'unauthorized'},401);
      let input;
      try {input = validate(await readLimited(request));}
      catch {return json({error:'invalid_payload'},400);}
      try {
        const success = input.result === 'ok';
        const result = await env.DB.prepare(UPSERT).bind(input.facility, input.checked_at,
          success ? input.checked_at : null, input.result,
          success ? JSON.stringify(input.cells) : null).run();
        return json({accepted:true,updated:result.meta.changes > 0});
      } catch {return json({error:'storage_unavailable'},503);}
    }
    if (url.pathname === '/availability' && request.method === 'GET') {
      const facility = url.searchParams.get('facility');
      if (!FACILITIES.has(facility)) return json({error:'invalid_facility'},400,origin);
      try {
        const row = await env.DB.prepare('SELECT * FROM facility_snapshots WHERE facility = ?')
          .bind(facility).first();
        if (!row) return json({facility,state:'no_data',cells:[]},200,origin);
        const stale = !row.success_at || Date.now()-Date.parse(row.success_at) > STALE_MS;
        const degraded = row.result !== 'ok' || stale;
        return json({schema_version:1,facility,checked_at:row.checked_at,success_at:row.success_at,
          state:degraded ? 'unverified' : 'source_checked',
          booking_verified:false,time_ranges_normalized:false,
          // Keep last good data in storage, but do not present it as current availability.
          cells:degraded ? [] : JSON.parse(row.payload || '[]')},200,origin);
      } catch {return json({error:'storage_unavailable'},503,origin);}
    }
    return json({error:'not_found'},404,origin);
  }
};
