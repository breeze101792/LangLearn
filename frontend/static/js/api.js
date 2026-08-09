// Thin fetch wrapper. All endpoints return {ok, data} or {ok:false, error}.

async function request(method, path, body) {
  const opts = { method, headers: { "Content-Type": "application/json" }, credentials: "same-origin" };
  if (body !== undefined) opts.body = JSON.stringify(body);
  let r;
  try {
    r = await fetch(path, opts);
  } catch (e) {
    return { ok: false, error: "network_error", data: null };
  }
  let data;
  try {
    data = await r.json();
  } catch (e) {
    return { ok: false, error: "invalid_json", data: null, status: r.status };
  }
  if (r.status === 401) {
    // future-proof for auth; for now treat as fatal
    return { ok: false, error: data?.error || "unauthorized", data: null, status: 401 };
  }
  if (!r.ok) {
    return { ok: false, error: data?.error || "http_error", data: data?.data ?? null, status: r.status };
  }
  return data;
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, body) => request("POST", path, body || {}),
  put: (path, body) => request("PUT", path, body || {}),
  del: (path) => request("DELETE", path),
};