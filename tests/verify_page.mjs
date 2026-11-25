// Drive the real page in a real browser, on a desktop and a phone viewport.
//
// Everything above this file tests the pipeline. This tests the page: that the
// WebAssembly module loads over HTTP and not only under node, that the stages
// render, that every example button works, that the decision explanations
// appear, that nothing throws into the console, and that the page is not wider
// than the phone it is being read on.
//
//   node tests/verify_page.mjs [--url http://localhost:5173/frontend]
//
// Needs a server already running and, for the model stages, the API. It is not
// part of the offline pytest suite for that reason: `make verify-page` runs it
// against a dev server, and CI runs it against a preview build.

import { chromium, devices } from "playwright";

const argv = process.argv.slice(2);
const flag = (name, fallback) => {
  const i = argv.indexOf(`--${name}`);
  return i === -1 ? fallback : argv[i + 1];
};

const URL = flag("url", "http://localhost:5173/frontend");
const SKIP_API = argv.includes("--no-api");

const FLAGSHIP = "I read the 2nd Dr. Lee lead a live band on Reading Rd at 10:30";
const NORMALISED =
  "I read the second Doctor Lee lead a live band on Reading Road at ten thirty";

let failures = 0;
const results = [];

function check(ok, label, detail = "") {
  results.push({ ok, label, detail });
  if (!ok) failures += 1;
  const mark = ok ? "  ok  " : " FAIL ";
  console.log(`${mark} ${label}${detail && !ok ? `\n        ${detail}` : ""}`);
}

async function text(page, selector) {
  const el = await page.$(selector);
  return el ? (await el.innerText()).replace(/\s+/g, " ").trim() : null;
}

/** Total bytes the browser actually pulled, by resource type. */
function trackTransfer(page, notFound) {
  const seen = new Map();
  page.on("response", async (response) => {
    const url = response.url();
    if (response.status() >= 400) notFound.push(`${response.status()} ${url}`);
    if (seen.has(url)) return;
    try {
      const body = await response.body();
      seen.set(url, {
        bytes: body.length,
        // What the wire carried, which is what a visitor waits for.
        encoded: Number(response.headers()["content-length"]) || body.length,
        type: url.split("?")[0].split(".").pop(),
      });
    } catch {
      /* redirects and aborted requests have no body */
    }
  });
  return seen;
}

/**
 * Open a decision's explanation the way a person on this device would.
 *
 * Hover on a pointer device, tap on a touch one -- the page supports both, and
 * the phone viewport is emulated with `hasTouch`, where a synthetic hover
 * proves nothing about what a thumb does.
 *
 * Aimed at the centre of the element's *first client rect* rather than its
 * bounding box. A highlighted span is inline, so on a narrow viewport
 * "Reading Road" wraps across two lines and the centre of its bounding box
 * falls in the gap between the two line boxes -- where the hit target is the
 * parent, not the span. A real finger lands on a glyph and has no such problem;
 * this is a faithful click, not a workaround for one.
 *
 * Scrolled to the middle of the viewport first, because the site has a fixed
 * navigation dock floating at the bottom centre and an element under it cannot
 * be clicked at that scroll position. `dockClear` below checks the dock
 * separately rather than this quietly forcing through it.
 */
async function activate(page, handle, touch) {
  const point = await handle.evaluate((el) => {
    el.scrollIntoView({ block: "center", behavior: "instant" });
    const rect = el.getClientRects()[0] ?? el.getBoundingClientRect();
    // getClientRects is in the *layout* viewport; synthetic input arrives in
    // the *visual* viewport, and on an emulated phone the two differ -- 31 px
    // on iPhone 13, which is three times the height of a line of text. Every
    // tap without this correction lands below the word it was aimed at and
    // silently hits nothing. Not a page bug: a finger lands on the glyph.
    const view = window.visualViewport;
    return {
      x: rect.left + rect.width / 2 - (view?.offsetLeft ?? 0),
      y: rect.top + rect.height / 2 - (view?.offsetTop ?? 0),
    };
  });
  if (touch) {
    // touchscreen.tap, not mouse.click: on a touch-emulated context the page
    // sees touch events, and a synthetic mouse click is not what a thumb does.
    // The page has to work for a thumb, which is the whole point of running
    // this viewport at all.
    await page.touchscreen.tap(point.x, point.y);
  } else {
    await page.mouse.move(point.x, point.y);
  }
  await page.waitForSelector(".fx-detail.is-open", { timeout: 8_000 });
}

async function run(label, device, viewport) {
  console.log(`\n── ${label} ──`);
  // Playwright's own Chromium is a 150 MB download that CI has cached and a
  // laptop usually has not. CHROME_CHANNEL=chrome drives the installed Google
  // Chrome instead, which is what this was developed against; unset, it uses
  // the bundled build.
  const channel = process.env.CHROME_CHANNEL || undefined;
  const browser = await chromium.launch(channel ? { channel } : {});
  const context = await browser.newContext(
    device ? { ...device } : { viewport },
  );
  const page = await context.newPage();
  const touch = Boolean(device?.hasTouch);
  const notFound = [];
  const transfer = trackTransfer(page, notFound);

  const consoleErrors = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(String(error)));

  const started = Date.now();
  await page.goto(URL, { waitUntil: "networkidle", timeout: 60_000 });
  const loadMs = Date.now() - started;

  // ---- stage 1, which runs in the tab and must not need the network --------
  await page.waitForSelector(".fx-stage", { timeout: 20_000 });
  const box = await page.inputValue("#fx-input");
  check(box === FLAGSHIP, "the box is prefilled with the flagship sentence", box);

  await page.waitForFunction(
    (expected) =>
      [...document.querySelectorAll(".fx-stage .fx-text")].some((el) =>
        el.innerText.replace(/\s+/g, " ").trim().includes(expected),
      ),
    NORMALISED,
    { timeout: 30_000 },
  );
  check(true, "WebAssembly normalisation ran in the browser");

  const marks = await page.$$(".fx-stage .fx-mark");
  check(marks.length >= 8, `every rewritten span is highlighted (${marks.length})`);

  // ---- let the pipeline settle before touching anything -------------------
  //
  // Interaction checks come after this, deliberately. `networkidle` fires
  // before the 650 ms debounce on stages 2 and 3 has even started, so a click
  // issued here lands while the response is still being rendered and the
  // coordinates computed a moment earlier are stale. A reader interacts with a
  // page that has stopped moving; so does this.
  let apiAnswered = false;
  let apiDown = false;
  if (!SKIP_API) {
    apiAnswered = await page
      .waitForFunction(
        () =>
          document.querySelector(".fx-phones") !== null ||
          document.querySelector(".fx-down") !== null,
        null,
        { timeout: 90_000 },
      )
      .then(() => true)
      .catch(() => false);
    apiDown = (await page.$(".fx-down")) !== null;
  }
  // And then one frame of quiet, so the grid has finished reflowing.
  await page.waitForTimeout(250);

  // ---- the decision explanations ------------------------------------------
  const settled = await page.$$(".fx-stage .fx-mark");
  await activate(page, settled[0], touch);
  const detail = await text(page, ".fx-detail.is-open");
  check(
    detail && detail.length > 40 && /Read as|beat/i.test(detail),
    touch
      ? "tapping a span explains the decision in words"
      : "hovering a span explains the decision in words",
    detail ?? "",
  );

  // ---- stages 2 and 3, from the API ---------------------------------------
  if (!SKIP_API) {
    const ok = apiAnswered;
    const down = apiDown ? await page.$(".fx-down") : null;
    if (down) {
      const message = await text(page, ".fx-down");
      check(
        message && message.length > 60,
        "when the service is down the page says so clearly",
        message ?? "",
      );
      check(
        (await page.$(".fx-stage .fx-text")) !== null,
        "and the stages that run locally still work",
      );
    } else {
      check(ok, "the model service answered");
      const phones = await text(page, ".fx-phones");
      check(
        phones?.startsWith("AY1 R EH1 D"),
        "the phoneme string is rendered",
        phones ?? "",
      );
      check(
        phones?.includes("R EH1 D IH0 NG R OW1 D"),
        "'Reading Road' is pronounced as the town, not the gerund",
      );
      const breaks = await page.$$(".fx-brk");
      check(breaks.length >= 1, `phrase breaks are marked (${breaks.length})`);
      const veto = await page.$(".fx-veto");
      check(
        veto !== null,
        "the suppressed break inside 'Doctor Lee' is shown, not hidden",
      );
      if (veto) {
        await activate(page, veto, touch);
        const why = await text(page, ".fx-detail.is-open");
        check(
          /proper name/i.test(why ?? ""),
          "and hovering it explains why",
          why ?? "",
        );
      }
    }
  }

  // ---- every example button ----------------------------------------------
  const buttons = await page.$$(".fx-example");
  check(buttons.length === 4, `four example buttons (${buttons.length})`);
  for (let i = 0; i < buttons.length; i += 1) {
    const fresh = (await page.$$(".fx-example"))[i];
    const label = (await fresh.innerText()).trim();
    const before = await page.inputValue("#fx-input");
    await fresh.click();
    // The first example is the flagship, which is already in the box, so its
    // click is a no-op on the value. Only wait for a change when one is due.
    const expected = await fresh.evaluate((el) => el.getAttribute("title"));
    const changed = await page
      .waitForFunction(
        (previous) => document.querySelector("#fx-input").value !== previous,
        before,
        { timeout: 8_000 },
      )
      .then(() => true)
      .catch(() => false);
    const value = await page.inputValue("#fx-input");
    check(
      changed || value === before,
      `example “${label}” sets the box`,
      `title=${expected}`,
    );
    // And stage 1 must produce something for it, within its own budget.
    const rendered = await page
      .waitForFunction(
        (box) => {
          const panels = [...document.querySelectorAll(".fx-stage .fx-text")];
          if (panels.length < 2) return false;
          const normalised = panels[1].innerText.trim();
          // Non-trivial, and actually a normalisation of what is in the box
          // rather than a leftover render of the previous example.
          return normalised.length > 20 && normalised.split(/\s+/).length >=
            Math.min(6, box.split(/\s+/).length);
        },
        value,
        { timeout: 20_000 },
      )
      .then(() => true)
      .catch(() => false);
    check(rendered, `example “${label}” normalises`);
  }

  // ---- the evaluation tables ---------------------------------------------
  const tables = await page.$$(".fx-eval table");
  check(tables.length >= 7, `the evaluation tables render (${tables.length})`);
  const body = await page.innerText("body");
  for (const needle of ["0.9895", "73.8%", "punctuation"]) {
    check(body.includes(needle), `the page quotes its saved results (${needle})`);
  }

  // ---- layout -------------------------------------------------------------
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  check(overflow <= 1, `the page does not scroll sideways (${overflow}px over)`);

  const tableScrolls = await page.evaluate(() =>
    [...document.querySelectorAll(".fx-scroll")].every(
      (el) => getComputedStyle(el).overflowX === "auto",
    ),
  );
  check(tableScrolls, "wide tables scroll inside their own box");

  // The site's fixed dock sits over whatever is beneath it. The page has to
  // leave room so that its own last element is not permanently underneath.
  const dockClear = await page.evaluate(() => {
    const dock = document.querySelector(".dock-inner");
    if (!dock) return true;
    window.scrollTo(0, document.body.scrollHeight);
    const last = document.querySelector(".fx-repos li:last-child");
    if (!last) return true;
    const a = dock.getBoundingClientRect();
    const b = last.getBoundingClientRect();
    return b.bottom < a.top || b.top > a.bottom;
  });
  check(dockClear, "the page leaves room for the site's fixed navigation");

  const tapTargets = await page.evaluate(() =>
    [...document.querySelectorAll(".fx-example")].every(
      (el) => el.getBoundingClientRect().height >= 28,
    ),
  );
  check(tapTargets, "the example buttons are big enough to tap");

  // ---- console ------------------------------------------------------------
  // A 404 for a favicon is the browser asking for something the site does not
  // have, on every page; anything else is the page asking for something that
  // should be there.
  const missing = notFound.filter((e) => !/favicon/i.test(e));
  check(missing.length === 0, "every resource the page asks for exists",
        missing.join("\n        "));

  const realErrors = consoleErrors.filter(
    (e) => !/favicon|404|ERR_INTERNET_DISCONNECTED/i.test(e),
  );
  check(
    realErrors.length === 0,
    "nothing threw into the console",
    realErrors.join("\n        "),
  );

  // ---- weight -------------------------------------------------------------
  let total = 0;
  const byType = {};
  for (const [, info] of transfer) {
    total += info.encoded;
    byType[info.type] = (byType[info.type] ?? 0) + info.encoded;
  }
  const kb = (n) => `${(n / 1024).toFixed(1)} KB`;
  console.log(`\n  ${label} weight: ${kb(total)} over ${transfer.size} requests`);
  for (const [type, bytes] of Object.entries(byType).sort((a, b) => b[1] - a[1])) {
    console.log(`    ${type.padEnd(6)} ${kb(bytes)}`);
  }
  console.log(`  load to network-idle: ${loadMs} ms`);

  await browser.close();
  return { total, loadMs };
}

const desktop = await run("desktop · 1440×900", null, { width: 1440, height: 900 });
const phone = await run("phone · iPhone 13", devices["iPhone 13"]);

console.log(
  `\n${failures === 0 ? "PASS" : `FAIL (${failures})`} — ` +
    `${results.length} checks across two viewports\n` +
    `desktop ${(desktop.total / 1024).toFixed(1)} KB, ` +
    `phone ${(phone.total / 1024).toFixed(1)} KB`,
);
process.exit(failures === 0 ? 0 : 1);
