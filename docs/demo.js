// The demo page: loads the engine and the extract from docs/local/, animates the book and
// its prints, and measures the replay rate from whole calls into the engine. WebAssembly has
// no clock, so every time here is JavaScript's performance.now() around a call.
//
// With ?play it starts playing at the largest batch, and with &post=URL it posts the page it
// leaves once the extract has played. With ?bench=N it runs T-WASM-1's
// browser side instead: a warm-up replay and N timed ones, each on a fresh book, then posts
// the record to the server that serves it (scripts/wasm_bench.py). Nothing else is timed there.
// Either posts only to the page's own origin: a post= URL on any other is ignored.

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const DEPTH_LEVELS = 15;
const TAPE_ROWS = 18;
const CHART_POINTS = 400;
const RATE_WINDOW = 120;   // Batches the rate is taken over.
const MEASURE_RUNS = 5;
const STATUS_NAMES = ["applied", "no_book_effect", "unknown_order", "locate_mismatch",
  "over_deduction", "duplicate_reference", "bad_side", "capacity_exhausted"];
const EXTRACT_STATUS = ["ok", "no_symbol", "many_symbols", "bad_framing"];

class Missing extends Error {}

// Where the page may post: the URL, resolved against the page, when it is on the page's own
// origin, else null. What the page posts holds the extract's book and tape.
const ownOrigin = (url) => {
  try {
    const resolved = new URL(url, location.href);
    return resolved.origin === location.origin ? resolved.href : null;
  } catch {
    return null;
  }
};

const now = () => performance.now();
const hex = (buffer) => [...new Uint8Array(buffer)].map((b) => b.toString(16).padStart(2, "0")).join("");
const sha256 = async (bytes) => hex(await crypto.subtle.digest("SHA-256", bytes));
const grouped = (n) => Math.round(n).toLocaleString("en-US");
const price = (p4) => (p4 / 10000).toFixed(p4 % 100 === 0 ? 2 : 4);
const median = (xs) => {
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};
const clockOf = (ns) => {
  const ms = Math.floor(ns / 1e6);
  const pad = (v, w = 2) => String(v).padStart(w, "0");
  return `${pad(Math.floor(ms / 3600000))}:${pad(Math.floor(ms / 60000) % 60)}:` +
         `${pad(Math.floor(ms / 1000) % 60)}.${pad(ms % 1000, 3)}`;
};

// The engine's C interface (src/demo_engine.hpp) over its linear memory, which never grows,
// so the typed views stay valid.
class Engine {
  constructor(module) {
    this.m = module;
    this.u8 = module.HEAPU8;
    this.u32 = module.HEAPU32;
  }

  load(bytes) {
    const at = this.m._demo_input(bytes.length);
    if (at === 0) {
      throw new Error(`the extract, ${grouped(bytes.length)} bytes, is larger than the ` +
                      `engine's ${grouped(this.m._demo_max_input_bytes())} bytes of input`);
    }
    this.u8.set(bytes, at);
    this.restart();
  }

  // A fresh book over the whole input, built here and never timed.
  restart() {
    const status = this.m._demo_load();
    if (status !== 0) {
      throw new Error(`the extract is not one symbol's: ${EXTRACT_STATUS[status] ?? status}`);
    }
  }

  replay() { return this.m._demo_replay(); }
  step(n) { return this.m._demo_step(n); }
  messages() { return this.m._demo_messages(); }
  applied() { return this.m._demo_applied(); }
  symbol() { return this.m.UTF8ToString(this.m._demo_symbol()).trim(); }
  lastTimestamp() { return this.m._demo_last_timestamp_ns(); }
  memoryBytes() { return this.u8.length; }
  statuses() {
    return Object.fromEntries(STATUS_NAMES.map((name, i) => [name, this.m._demo_status(i)]));
  }
  digest() {
    const high = this.m._demo_digest_high() >>> 0;
    const low = this.m._demo_digest_low() >>> 0;
    return high.toString(16).padStart(8, "0") + low.toString(16).padStart(8, "0");
  }

  u64(word) { return this.u32[word] + this.u32[word + 1] * 4294967296; }

  depth(side, levels) {
    const count = this.m._demo_depth(side, levels);
    const base = this.m._demo_depth_buffer() >>> 2;
    const out = [];
    for (let i = 0; i < count; ++i) {
      const w = base + i * 4;
      out.push({ price: this.u32[w], orders: this.u32[w + 1], shares: this.u64(w + 2) });
    }
    return out;
  }

  prints() { return this.m._demo_prints(); }
  // A P's side is the message's field, which Nasdaq has sent as "B" on every P since
  // 2014-07-14 whatever side the hidden order rested on, so a P shows no side, as a cross.
  print(n) {
    const at = this.m._demo_print(n);
    if (at === 0) return null;
    const w = at >>> 2;
    const kind = String.fromCharCode(this.u8[at + 20]);
    return { ns: this.u64(w), shares: this.u64(w + 2), price: this.u32[w + 4], kind,
             side: kind === "P" ? " " : String.fromCharCode(this.u8[at + 21]) };
  }
}

async function loadEngine() {
  let create;
  try {
    ({ default: create } = await import("./local/itch_demo.mjs"));
  } catch (error) {
    throw new Missing("The engine is not built: docs/local/itch_demo.mjs is missing. " +
                      "Run python3 scripts/demo.py build.");
  }
  const engine = new Engine(await create());
  const response = await fetch("local/engine.json");
  engine.info = response.ok ? await response.json() : {};
  const wasm = await fetch("local/itch_demo.wasm");
  engine.wasmSha256 = wasm.ok ? await sha256(await wasm.arrayBuffer()) : "unread";
  return engine;
}

async function loadExtract() {
  const meta = await fetch("local/extract.json");
  if (!meta.ok) {
    throw new Missing("The extract is missing: docs/local/extract.json was not found. " +
                      "Run python3 scripts/demo.py extract with a day under data/.");
  }
  const manifest = await meta.json();
  const response = await fetch(`local/${manifest.extract_file}`);
  if (!response.ok) {
    throw new Missing(`docs/local/${manifest.extract_file} is missing. Run python3 ` +
                      "scripts/demo.py extract again.");
  }
  const bytes = new Uint8Array(await response.arrayBuffer());
  const digest = await sha256(bytes);
  if (digest !== manifest.extract_sha256) {
    throw new Error(`docs/local/${manifest.extract_file} is not the extract extract.json ` +
                    "describes: its SHA-256 differs. Run python3 scripts/demo.py extract again.");
  }
  return { manifest, bytes, sha256: digest };
}

// The smallest step performance.now() takes here, from many reads in a row.
function clockStep() {
  let smallest = Infinity;
  let last = now();
  for (let i = 0; i < 200000; ++i) {
    const t = now();
    if (t > last) {
      smallest = Math.min(smallest, t - last);
      last = t;
    }
  }
  return smallest;
}

function showClock() {
  const step = clockStep();
  $("clock-step").textContent = Number.isFinite(step)
    ? `${(step * 1000).toFixed(1)} µs, as measured on this page` : "an amount it could not measure";
  $("isolated").textContent = crossOriginIsolated ? "is" : "is not";
  return step;
}

// Each whole replay on a fresh book is one batch: the book is built before the clock starts.
function timedReplays(engine, runs) {
  const spans = [];
  let messages = 0;
  for (let i = 0; i < runs; ++i) {
    engine.restart();
    const start = now();
    messages = engine.replay();
    spans.push(now() - start);
  }
  return { spans, messages };
}

// The animation.
class Player {
  constructor(engine, extract) {
    this.engine = engine;
    this.extract = extract;
    this.playing = false;
    this.batches = [];
    this.tapeFrom = 0;
    this.chart = [];
  }

  restart() {
    this.engine.restart();
    this.batches = [];
    this.tapeFrom = 0;
    this.chart = [];
    $("tape").tBodies[0].replaceChildren();
    this.render();
  }

  batch() {
    const size = Number($("batch").value);
    const start = now();
    const applied = this.engine.step(size);
    const span = now() - start;
    if (applied > 0) {
      this.batches.push({ applied, span });
      if (this.batches.length > RATE_WINDOW) this.batches.shift();
    }
    return applied;
  }

  frame() {
    if (!this.playing) return;
    if (this.batch() === 0) {
      this.setPlaying(false);
      this.render();
      this.finished();
      return;
    }
    this.render();
    requestAnimationFrame(() => this.frame());
  }

  // With ?play&post=URL, the page as it stands once the whole extract has played, for
  // scripts/test_demo_engine.py, which cannot look at a headless browser's screen.
  finished() {
    const post = params.get("post") ? ownOrigin(params.get("post")) : null;
    if (params.has("play") && post) {
      fetch(post, { method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ dom: document.documentElement.outerHTML }) });
    }
  }

  setPlaying(on) {
    this.playing = on;
    $("play").textContent = on ? "Pause" : "Play";
    if (on) requestAnimationFrame(() => this.frame());
  }

  render() {
    const e = this.engine;
    const done = e.applied() === e.messages();
    $("clock").textContent = clockOf(e.lastTimestamp());
    $("progress").textContent = `${grouped(e.applied())} of ${grouped(e.messages())}`;
    const applied = this.batches.reduce((sum, b) => sum + b.applied, 0);
    const span = this.batches.reduce((sum, b) => sum + b.span, 0);
    $("rate").textContent = span >= 20
      ? `${grouped(applied / span * 1000)} messages/s over the last ${this.batches.length} ` +
        `batches, ${grouped(applied)} messages in ${span.toFixed(1)} ms`
      : "waiting for 20 ms of batches";
    this.renderDepth();
    this.renderTrades();
    if (done) {
      const want = this.extract.manifest.digest;
      const got = e.digest();
      $("digest").innerHTML = got === want
        ? `<span class="match">${got}, the same as the native build's</span>`
        : `<span class="differs">${got}, not the native build's ${want}</span>`;
    } else {
      $("digest").textContent = "shown once every message is applied";
    }
  }

  renderDepth() {
    const bids = this.engine.depth(0, DEPTH_LEVELS);
    const asks = this.engine.depth(1, DEPTH_LEVELS);
    const most = Math.max(1, ...bids.map((l) => l.shares), ...asks.map((l) => l.shares));
    const rows = [];
    for (let i = 0; i < DEPTH_LEVELS; ++i) {
      const b = bids[i];
      const a = asks[i];
      const bar = (level, side) => level
        ? `<td class="bar ${side}"><span style="width:${(level.shares / most * 100).toFixed(1)}%"></span><b>${grouped(level.shares)}</b></td>`
        : "<td></td>";
      rows.push(`<tr><td>${b ? b.orders : ""}</td>${bar(b, "bid")}` +
                `<td class="bid">${b ? price(b.price) : ""}</td>` +
                `<td class="ask">${a ? price(a.price) : ""}</td>${bar(a, "ask")}` +
                `<td>${a ? a.orders : ""}</td></tr>`);
    }
    $("depth").tBodies[0].innerHTML = rows.join("");
  }

  renderTrades() {
    const total = this.engine.prints();
    const body = $("tape").tBodies[0];
    for (let n = Math.max(this.tapeFrom, total - TAPE_ROWS); n < total; ++n) {
      const p = this.engine.print(n);
      if (!p) continue;
      this.chart.push(p);
      const row = body.insertRow(0);
      const side = p.side === " " ? "side-cross" : `side-${p.side}`;
      row.innerHTML = `<td>${clockOf(p.ns)}</td><td class="${side}">${price(p.price)}</td>` +
                      `<td>${grouped(p.shares)}</td><td>${p.kind}</td>`;
    }
    this.tapeFrom = total;
    while (body.rows.length > TAPE_ROWS) body.deleteRow(body.rows.length - 1);
    if (this.chart.length > CHART_POINTS) this.chart.splice(0, this.chart.length - CHART_POINTS);
    this.drawChart();
  }

  drawChart() {
    const canvas = $("trade-chart");
    const ctx = canvas.getContext("2d");
    const { width, height } = canvas;
    ctx.fillStyle = "#11151a";
    ctx.fillRect(0, 0, width, height);
    if (this.chart.length === 0) return;
    const prices = this.chart.map((p) => p.price);
    const low = Math.min(...prices);
    const high = Math.max(...prices);
    const span = Math.max(high - low, 100);
    const colour = { B: "#3fb68b", S: "#e0794a", " ": "#a3adb9" };
    ctx.font = "11px ui-monospace, monospace";
    ctx.fillStyle = "#9aa6b4";
    ctx.fillText(price(high), 4, 12);
    ctx.fillText(price(low), 4, height - 4);
    this.chart.forEach((p, i) => {
      const x = 60 + (i / Math.max(CHART_POINTS - 1, 1)) * (width - 70);
      const y = 8 + (1 - (p.price - low) / span) * (height - 16);
      ctx.fillStyle = colour[p.side] ?? "#a3adb9";
      ctx.fillRect(x - 1.5, y - 1.5, 3, 3);
    });
  }
}

function showNumbers(numbers) {
  const linux = numbers.linux;
  const into = $("linux-numbers");
  if (!linux || linux.missing) {
    into.innerHTML = `<p class="missing">Missing: ${linux ? linux.missing : "docs/numbers.json has no Linux slot"}.</p>`;
  } else {
    const l = linux.latency;
    into.innerHTML = `<dl class="facts">
      <dt>Host</dt><dd>${linux.host}, ${linux.cpu}, ${linux.kernel}</dd>
      <dt>Book</dt><dd>${linux.design}, whole day ${linux.dataset}, the file in memory</dd>
      <dt>Throughput</dt><dd>${linux.throughput_messages_per_s} messages/s, median of the repetitions, CV ${linux.throughput_cv}</dd>
      <dt>apply() latency</dt><dd>p50 ${l.p50.median_ns} ns, p99 ${l.p99.median_ns} ns, p99.9 ${l["p99.9"].median_ns} ns</dd>
      <dt>Book memory</dt><dd>${linux.book_memory_mib} MiB</dd>
      <dt>Pinning</dt><dd>${linux.pinning}</dd>
      <dt>Build</dt><dd>${linux.compiler}, ${linux.flags}</dd>
      <dt>Commit</dt><dd>${linux.commit}, run ${linux.run_id}</dd>
      <dt>Source</dt><dd>${linux.file}</dd></dl>`;
  }
  const wasm = numbers.wasm_macos;
  const there = $("wasm-numbers");
  if (!wasm || wasm.missing) {
    there.innerHTML = `<p class="missing">Missing: ${wasm ? wasm.missing : "docs/numbers.json has no T-WASM-1 slot"}.</p>`;
  } else {
    there.innerHTML = `<dl class="facts">
      <dt>Host</dt><dd>${wasm.host}, ${wasm.cpu}</dd>
      <dt>Browser</dt><dd>${wasm.browser}</dd>
      <dt>Extract</dt><dd>${wasm.extract}</dd>
      <dt>Native</dt><dd>${wasm.native_messages_per_s} messages/s</dd>
      <dt>WebAssembly</dt><dd>${wasm.wasm_messages_per_s} messages/s</dd>
      <dt>WebAssembly's time over native's</dt><dd>${wasm.ratio}, against ${wasm.bar}</dd>
      <dt>Verdict</dt><dd class="verdict">${wasm.verdict}</dd>
      <dt>Commit</dt><dd>${wasm.commit}</dd>
      <dt>Source</dt><dd>${wasm.file}</dd></dl>`;
  }
}

async function loadNumbers() {
  try {
    const response = await fetch("numbers.json");
    showNumbers(response.ok ? await response.json() : {});
  } catch (error) {
    showNumbers({});
  }
}

function setUp(reason) {
  $("setup").hidden = false;
  $("setup-reason").textContent = reason;
}

async function interactive() {
  showClock();
  let engine;
  let extract;
  try {
    [engine, extract] = await Promise.all([loadEngine(), loadExtract()]);
    engine.load(extract.bytes);
  } catch (error) {
    setUp(error.message);
    return;
  }
  $("demo").hidden = false;
  $("symbol").textContent = engine.symbol();
  $("day").textContent = extract.manifest.day;
  const player = new Player(engine, extract);
  player.render();
  $("play").onclick = () => player.setPlaying(!player.playing);
  $("step").onclick = () => { player.setPlaying(false); player.batch(); player.render(); };
  $("restart").onclick = () => { player.setPlaying(false); player.restart(); };
  if (params.has("play")) {
    $("batch").value = "100000";
    player.setPlaying(true);
  }
  $("measure").onclick = () => {
    player.setPlaying(false);
    for (const id of ["measure", "play", "step", "restart"]) $(id).disabled = true;
    $("measure-result").textContent = "Measuring...";
    setTimeout(() => {
      const { spans, messages } = timedReplays(engine, MEASURE_RUNS);
      const ms = median(spans);
      $("measure-result").textContent =
        `${grouped(messages / ms * 1000)} messages/s, the median of ${MEASURE_RUNS} whole ` +
        `replays of ${grouped(messages)} messages: ${spans.map((s) => s.toFixed(1)).join(", ")} ms. ` +
        "A browser figure on this machine, not a native one.";
      player.restart();
      for (const id of ["measure", "play", "step", "restart"]) $(id).disabled = false;
    }, 20);
  };
}

// T-WASM-1's browser side. The record goes to the server, which writes it and ends the run.
async function benchmark(runs) {
  const post = ownOrigin(params.get("post") || "/result");
  const record = { engine: "wasm", user_agent: navigator.userAgent,
                   cross_origin_isolated: crossOriginIsolated, refusals: [] };
  try {
    record.clock_step_ms = showClock();
    const [engine, extract] = await Promise.all([loadEngine(), loadExtract()]);
    engine.load(extract.bytes);
    Object.assign(record, {
      wasm_sha256: engine.wasmSha256, engine_info: engine.info,
      memory_bytes: engine.memoryBytes(), max_input_bytes: engine.m._demo_max_input_bytes(),
      extract: { symbol: engine.symbol(), day: extract.manifest.day, bytes: extract.bytes.length,
                 messages: engine.messages(), sha256: extract.sha256 },
    });
    const warm = timedReplays(engine, 1);
    record.warmup = { ms: warm.spans[0], messages: warm.messages };
    record.digest = engine.digest();
    record.statuses = engine.statuses();
    const timed = [];
    for (let i = 0; i < runs; ++i) {
      const one = timedReplays(engine, 1);
      timed.push({ ms: one.spans[0], messages: one.messages, digest: engine.digest() });
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    record.repetitions = timed;
    record.memory_bytes_after = engine.memoryBytes();
    if (record.digest !== extract.manifest.digest) {
      record.refusals.push(`digest ${record.digest} is not the native extract's ${extract.manifest.digest}`);
    }
    if (timed.some((t) => t.digest !== record.digest || t.messages !== warm.messages)) {
      record.refusals.push("a repetition differs from the warm-up");
    }
  } catch (error) {
    record.refusals.push(`the page failed: ${error.message}`);
  }
  document.body.dataset.bench = "done";
  if (post) {
    await fetch(post, { method: "POST", headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(record) });
  }
}

loadNumbers();
if (params.has("bench")) {
  benchmark(Math.max(3, Number(params.get("bench")) || 21));
} else {
  interactive();
}
