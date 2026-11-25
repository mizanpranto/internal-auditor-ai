"""Visual system for Internal Auditor: CSS plus pure HTML builders (no Streamlit imports).

Rules that keep this safe and renderable:
* every value that can come from a document or a model goes through esc();
* each builder returns ONE line of HTML with no blank lines, so Streamlit's
  markdown parser treats it as raw HTML instead of text or a code block;
* sizes are written in px in CSS below and converted to rem by scale_px(), so one
  number (UI_SCALE) controls how big the whole interface is.
"""

from __future__ import annotations

import html
import re
from collections import Counter
from typing import Optional

from .risk import level_for
from .schemas import Assessment, Control, RiskItem
from .scoring import STATUS_ORDER

STATUS_CLS = {"Met": "met", "Partially met": "half", "Not met": "no", "No evidence": "none", "Not applicable": "na"}

APP_NAME = "Internal Auditor"
UI_SCALE = 0.8        # 1.0 = the original size; 0.8 = 20% smaller everywhere
MIN_FONT_PX = 11      # scaled text never goes below this, so small labels stay readable
PAGE_MAX_PX = 1500    # widest the content area gets (physical px, not scaled)

CSS = """
:root {
  --bg: #070B1A; --panel: #121A3C; --panel2: #0D1330; --line: #232C5E; --line2: #1A2250;
  --text: #EEF1FF; --soft: #C4CBEA; --muted: #8E98C4; --dim: #5D6797;
  --violet: #8B7CFF; --teal: #4FD1C5; --green: #5EE6A4; --amber: #F5C45E; --red: #FF7B7B;
}
.stApp {
  background:
    radial-gradient(900px 520px at 78% -8%, rgba(139,124,255,.16), transparent 60%),
    radial-gradient(760px 480px at 8% 108%, rgba(79,209,197,.08), transparent 60%),
    var(--bg);
}
header[data-testid="stHeader"] { background: transparent; }
[data-testid="stAppDeployButton"], [data-testid="stDecoration"], footer { display: none !important; }
.stApp .block-container { padding: 2.4rem 2.4rem 4rem; }
.stApp :focus-visible { outline: 2px solid var(--violet); outline-offset: 2px; }

/* ---- sidebar ------------------------------------------------------------ */
[data-testid="stSidebar"] { background: #0C122B; border-right: 1px solid var(--line2); }
[data-testid="stSidebar"] .block-container, [data-testid="stSidebarContent"] { padding-top: 1.4rem; }
.brand { display: flex; align-items: center; gap: 12px; margin: 0 0 22px; }
.brand svg { width: 34px; height: 34px; flex: none; }
.brand b { display: block; font-size: 19px; line-height: 1.1; letter-spacing: -.01em; color: var(--text); }
.brand span { display: block; font-size: 12.5px; color: var(--muted); margin-top: 2px; }
.sbstatus { padding: 16px 18px; border-radius: 12px; font-size: 18px; line-height: 1.25; margin: 0 0 14px; }
.sbstatus.ok { background: #14291F; color: #D5F6E3; }
.sbstatus.bad { background: #3A1823; color: #FFD9DE; }
.sbstatus small { display: block; font-size: 13px; opacity: .8; margin-top: 4px; }
.sbmodels { color: var(--muted); font-size: 15px; line-height: 1.55; margin: 0 0 14px; }
.sbrow { font-size: 16px; font-weight: 600; margin: 0 0 12px; color: var(--text); }
.sbrow code { font-size: 13px; font-weight: 400; background: #1B2244; color: var(--text); padding: 3px 8px; border-radius: 6px; margin-left: 4px; }
.sbhead { font-size: 15px; color: var(--soft); margin: 20px 0 4px; }
[data-testid="stSidebar"] [role="radiogroup"] { gap: 2px; }
[data-testid="stSidebar"] [role="radiogroup"] label { padding: 6px 4px; border-radius: 8px; }
[data-testid="stSidebar"] [role="radiogroup"] label:hover { background: rgba(139,124,255,.08); }
[data-testid="stSidebar"] [role="radiogroup"] label p { font-size: 17px; }

/* ---- page header ----------------------------------------------------------- */
.eyebrow { font-size: 13px; font-weight: 700; letter-spacing: .2em; text-transform: uppercase; color: #5961B5; margin: 0 0 8px; }
.pg-title { font-size: clamp(30px, 3.6vw, 46px); font-weight: 750; letter-spacing: -.02em; line-height: 1.08; color: #fff; margin: 0 0 10px; }
.pg-sub { font-size: 19px; color: var(--soft); margin: 0 0 26px; max-width: 60em; line-height: 1.45; }

/* ---- cards ------------------------------------------------------------------ */
.kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 24px; margin: 0 0 28px; }
.kpi, .panel { background: linear-gradient(165deg, #151D42 0%, #0F1634 100%); border: 1px solid var(--line); border-radius: 24px; }
.kpi { padding: 24px 26px 22px; }
.kpi .l { font-size: 15px; font-weight: 700; letter-spacing: .03em; text-transform: uppercase; color: #AEB7E6; }
.kpi .v { font-size: 52px; font-weight: 750; letter-spacing: -.03em; line-height: 1; margin: 18px 0 16px; color: #fff; }
.kpi .v small { font-size: 22px; color: var(--muted); font-weight: 600; letter-spacing: 0; }
.kpi .c { font-size: 16px; color: var(--muted); }
.panel { padding: 24px 28px; margin: 0 0 24px; }
.panel .t { font-size: 22px; font-weight: 700; color: #fff; margin: 0 0 6px; }
.panel .s { font-size: 16px; color: var(--muted); margin: 0; line-height: 1.5; }
.panel .s + .body { margin-top: 18px; }
.sect { font-size: 22px; font-weight: 700; color: #fff; margin: 4px 0 4px; }
.sect-sub { font-size: 15.5px; color: var(--muted); margin: 0 0 14px; }
.hr { height: 1px; background: var(--line); margin: 26px 0; border: 0; }

/* ---- chips ------------------------------------------------------------------- */
.chip { display: inline-flex; align-items: center; gap: 8px; font-size: 14px; font-weight: 600; padding: 7px 14px; border-radius: 999px; white-space: nowrap; }
.chip i { width: 8px; height: 8px; border-radius: 50%; background: currentColor; display: inline-block; }
.chip.ok, .chip.met { background: rgba(94,230,164,.12); color: var(--green); border: 1px solid rgba(94,230,164,.35); }
.chip.half { background: rgba(245,196,94,.12); color: var(--amber); border: 1px solid rgba(245,196,94,.35); }
.chip.no, .chip.bad { background: rgba(255,123,123,.12); color: var(--red); border: 1px solid rgba(255,123,123,.35); }
.chip.na { background: rgba(120,160,255,.10); color: #9DB7F5; border: 1px dashed rgba(120,160,255,.45); }
.chip.none, .chip.idle { background: rgba(154,163,199,.10); color: #A6AFD2; border: 1px solid rgba(154,163,199,.28); }
.chip.info { background: rgba(139,124,255,.14); color: #B9B0FF; border: 1px solid rgba(139,124,255,.4); }
.chip.sm { font-size: 12.5px; padding: 4px 10px; }
.model-line { font-size: 20px; font-weight: 700; margin: 18px 0 10px; color: #fff; }
.model-line code, .kv code { font-size: 15px; font-weight: 400; background: #1B2244; padding: 4px 9px; border-radius: 6px; margin-left: 6px; color: var(--text); }
.kv { font-size: 16px; color: var(--soft); line-height: 1.7; }

/* ---- activity / lists ---------------------------------------------------------- */
.act { list-style: none; margin: 0; padding: 0; }
.act li { display: flex; gap: 12px; align-items: baseline; padding: 9px 0; border-bottom: 1px solid var(--line2); font-size: 16px; color: var(--text); }
.act li:last-child { border-bottom: 0; }
.act time { flex: none; font-size: 13px; color: var(--dim); min-width: 108px; }
.stat-line { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; font-size: 18px; padding: 10px 0; color: var(--text); }
.stat-line b { font-weight: 700; flex: none; white-space: nowrap; }
.note { background: rgba(139,124,255,.10); border: 1px solid rgba(139,124,255,.35); color: #D6D1FF; border-radius: 14px; padding: 14px 18px; font-size: 15.5px; line-height: 1.5; margin: 0 0 22px; }
.note.warn { background: rgba(245,196,94,.09); border-color: rgba(245,196,94,.4); color: #F7E2A9; }
.note.bad { background: rgba(255,123,123,.09); border-color: rgba(255,123,123,.4); color: #FFD3D3; }
.empty { border: 1.5px dashed #2D3874; border-radius: 20px; padding: 34px 30px; text-align: center; color: var(--soft); font-size: 17px; line-height: 1.55; margin: 8px 0 22px; }
.empty b { display: block; font-size: 21px; color: #fff; margin-bottom: 6px; }

/* ---- domain bars ---------------------------------------------------------------- */
.dom { display: grid; grid-template-columns: 150px minmax(0, 1fr) 54px; gap: 16px; align-items: center; padding: 9px 0; font-size: 16px; }
.dom .bar { display: flex; height: 14px; border-radius: 7px; overflow: hidden; background: #1A2250; }
.dom .bar span { display: block; height: 100%; }
.dom .pct { text-align: right; font-weight: 700; color: #fff; }
.seg-met { background: var(--green); } .seg-half { background: var(--amber); } .seg-no { background: var(--red); } .seg-none { background: #3A4478; }
.legend { display: flex; flex-wrap: wrap; gap: 18px; font-size: 14px; color: var(--soft); margin-top: 10px; }
.legend span { display: inline-flex; align-items: center; gap: 7px; }
.legend i { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }

/* ---- assessment card -------------------------------------------------------------- */
.acard { background: linear-gradient(165deg, #141C40 0%, #0F1634 100%); border: 1px solid var(--line); border-radius: 20px; padding: 20px 24px 18px; margin: 14px 0 6px; }
.acard .top { display: flex; justify-content: space-between; gap: 14px; flex-wrap: wrap; align-items: center; }
.acard .id { display: inline-block; font-size: 14px; font-weight: 700; color: #B9B0FF; background: rgba(139,124,255,.14); border-radius: 8px; padding: 3px 9px; margin-right: 10px; }
.acard .ttl { font-size: 20px; font-weight: 700; color: #fff; }
.acard .chips { display: flex; gap: 8px; flex-wrap: wrap; }
.acard .dm { font-size: 14px; color: var(--dim); margin: 6px 0 0; }
.acard blockquote { margin: 16px 0 6px; padding: 4px 0 4px 16px; border-left: 3px solid var(--green); font-size: 16.5px; line-height: 1.6; color: #E8EBFF; }
.stApp .acard blockquote mark { background: rgba(245,196,94,.22); color: #FFF3CF; padding: 1px 3px; border-radius: 3px; }
.acard .src { margin: 2px 0 0 19px; font-size: 14px; color: var(--muted); }
.acard .src b { color: var(--green); font-weight: 600; }
.acard .why { margin: 14px 0 0; font-size: 16px; line-height: 1.55; color: var(--soft); }
.acard .gap { margin: 10px 0 0; font-size: 16px; line-height: 1.55; color: #FFC9C9; }
.acard .gap b { color: #FFA6A6; }
.acard .flag { margin: 12px 0 0; padding: 10px 14px; background: rgba(255,123,123,.10); border: 1px solid rgba(255,123,123,.35); border-radius: 10px; font-size: 14.5px; color: #FFD0D0; }
.acard .rev { margin: 12px 0 0; font-size: 14px; color: var(--muted); }

.pass { border-left: 3px solid #39438A; background: rgba(139,124,255,.06); border-radius: 0 10px 10px 0; padding: 10px 14px; margin: 0 0 10px; font-size: 15px; line-height: 1.55; color: var(--soft); }
.pass small { display: block; color: var(--dim); font-size: 12.5px; margin-bottom: 3px; }
/* ---- heat map ----------------------------------------------------------------------- */
.heat { display: grid; grid-template-columns: 26px repeat(5, minmax(0, 1fr)); gap: 5px; align-items: center; max-width: 440px; }
.heat .ax { font-size: 13px; color: var(--muted); text-align: center; }
.hc { height: 52px; border-radius: 8px; display: grid; place-items: center; font-size: 18px; font-weight: 700; }
.h-Critical { background: #E5484D; color: #fff; } .h-High { background: #E98A5F; color: #2B1006; }
.h-Medium { background: #E8C560; color: #2D2305; } .h-Low { background: #4A8F76; color: #04170F; }
.hc.void { opacity: .22; }

/* ---- native widget polish ---------------------------------------------------------------- */
.stApp [data-testid="stBaseButton-primary"] { background: linear-gradient(135deg, #8B7CFF, #6C5CE7); border: 0; color: #fff; font-weight: 600; border-radius: 10px; }
.stApp [data-testid="stBaseButton-primary"]:hover { filter: brightness(1.1); }
.stApp [data-testid="stBaseButton-secondary"] { background: #131B3F; border: 1px solid var(--line); color: var(--text); border-radius: 10px; font-weight: 500; }
.stApp [data-testid="stBaseButton-secondary"]:hover { border-color: var(--violet); color: #fff; }
.stApp [data-testid="stFileUploaderDropzone"] { background: #0F1634; border: 1.5px dashed #2D3874; border-radius: 16px; }
.stApp [data-testid="stExpander"] details { background: #0F1634; border: 1px solid var(--line); border-radius: 14px; }
.stApp [data-testid="stDataFrame"] { border: 1px solid var(--line); border-radius: 14px; overflow: hidden; }
.stApp button[role="tab"] p { font-size: 16px; font-weight: 600; }
.stApp [data-baseweb="tab-highlight"] { background: var(--violet); }
.stApp [data-testid="stProgress"] > div > div > div { background: linear-gradient(90deg, #8B7CFF, #4FD1C5); }

/* ---- responsive: nothing may be wider than its container ----------------------------- */
.stApp img, .stApp svg, .stApp iframe { max-width: 100%; }
.panel, .kpi, .acard, .pass, .note, .empty, .act li, .stat-line, .kv { overflow-wrap: anywhere; }
.brand b { overflow-wrap: anywhere; }

/* laptop / small desktop */
@media (max-width: 1100px) {
  .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
  .dom { grid-template-columns: 120px minmax(0, 1fr) 46px; }
  .stApp .block-container { padding: 2rem 1.6rem 3.5rem; }
}
/* tablet and phone: every row of columns becomes one column */
@media (max-width: 820px) {
  .stApp .block-container { padding: 3.2rem 1.1rem 3rem; }  /* room for the sidebar toggle in the header */
  .stApp [data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; row-gap: .9rem; }
  .stApp [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
  .stApp [data-testid="stHorizontalBlock"] > [data-testid="column"] { flex: 1 1 100% !important; width: 100% !important; min-width: 100% !important; }
  .kpi { padding: 18px 20px; }
  .panel { padding: 20px 20px; }
  .acard { padding: 16px 18px 14px; }
  .pg-sub { font-size: 17px; margin-bottom: 20px; }
  .act li { flex-wrap: wrap; row-gap: 2px; }
  .act time { min-width: 0; width: 100%; }
}
/* phone */
@media (max-width: 480px) {
  .stApp .block-container { padding: 3rem .9rem 2.5rem; }
  .kpis { grid-template-columns: minmax(0, 1fr); }
  .kpi .v { font-size: 40px; margin: 12px 0 10px; }
  .dom { grid-template-columns: minmax(0, 1fr) 42px; gap: 4px 10px; }
  .dom > span:first-child { grid-column: 1 / -1; }  /* label on its own line, bar and % below */
  .eyebrow { letter-spacing: .12em; }
  .acard .ttl { font-size: 18px; }
  .acard .top { gap: 8px; }
  .chip { padding: 6px 11px; }
  .hc { height: 44px; font-size: 16px; }
}
"""

_PX = re.compile(r"(@media[^{]*)|(font-size\s*:\s*)(\d*\.?\d+)px|(?<![\w.#])(\d*\.?\d+)px")


def scale_px(css: str, scale: float = UI_SCALE, min_font_px: float = MIN_FONT_PX) -> str:
    """Convert px sizes to rem. With the root font size at `scale` x 16px, everything shrinks by `scale`.

    Media-query conditions stay in px (they describe the screen, not the design), hairlines of 2px or less
    stay crisp, and font sizes never drop below `min_font_px`.
    """
    root = 16 * scale

    def sub(m: re.Match) -> str:
        if m.group(1):
            return m.group(1)
        if m.group(2):
            return f"{m.group(2)}{max(float(m.group(3)) * scale, min_font_px) / root:.3f}rem"
        value = float(m.group(4))
        return m.group(0) if value <= 2 else f"{value / 16:.3f}rem"

    return _PX.sub(sub, css)


def css_block() -> str:
    body = "\n".join(line for line in CSS.splitlines() if line.strip())
    root = f"html {{ font-size: {UI_SCALE * 100:g}% !important; }}"
    page = f".stApp .block-container {{ max-width: {PAGE_MAX_PX}px; }}"  # physical px: big screens use the room freed by the smaller type
    return f"<style>{root}\n{scale_px(body)}\n{page}</style>"


def esc(value) -> str:
    """HTML-escape untrusted text (documents, model output).

    Whitespace runs, including newlines, become a single space: line breaks would otherwise be
    stripped by one_line() and glue words together, and blank lines would break Streamlit's
    markdown parser. '$' is neutralised so Streamlit does not try to render maths.
    """
    text = " ".join(("" if value is None else str(value)).split())
    return html.escape(text, quote=True).replace("$", "&#36;")


def one_line(s: str) -> str:
    return re.sub(r"\s*\n\s*", "", s)


# ------------------------------------------------------------------ sidebar
LOGO = (
    '<svg viewBox="0 0 40 40" aria-hidden="true"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0" stop-color="#8B7CFF"/><stop offset="1" stop-color="#4FD1C5"/></linearGradient></defs>'
    '<rect x="2" y="2" width="36" height="36" rx="10" fill="url(#g)"/>'
    '<path d="M12 21l6 6 11-13" fill="none" stroke="#0B1026" stroke-width="3.6" stroke-linecap="round" stroke-linejoin="round"/></svg>'
)


def brand_html() -> str:
    return f'<div class="brand">{LOGO}<div><b>{esc(APP_NAME)}</b><span>Local compliance agent</span></div></div>'


def sidebar_status_html(health: dict, llm_model: str, embed_model: str) -> str:
    if health.get("ok"):
        box = '<div class="sbstatus ok">Ollama connected</div>'
        models = ", ".join(health.get("models", [])) or "no models installed"
        avail = f'<div class="sbmodels">Available: {esc(models)}</div>'
    else:
        box = '<div class="sbstatus bad">Ollama not running<small>Open the Ollama app or run: ollama serve</small></div>'
        avail = ""
    rows = f'<div class="sbrow">LLM: <code>{esc(llm_model)}</code></div><div class="sbrow">Embeddings: <code>{esc(embed_model)}</code></div>'
    return box + avail + rows + '<div class="sbhead">Workspace</div>'


# ------------------------------------------------------------------ page parts
def page_header(title: str, subtitle: str, eyebrow: Optional[str] = None) -> str:
    eyebrow = eyebrow or f"{APP_NAME} / Intelligence workspace"
    return f'<div class="eyebrow">{esc(eyebrow)}</div><div class="pg-title" role="heading" aria-level="1">{esc(title)}</div><div class="pg-sub">{esc(subtitle)}</div>'


def kpi_cards(items: list[tuple[str, str, str]]) -> str:
    cards = "".join(
        f'<div class="kpi"><div class="l">{esc(l)}</div><div class="v">{v}</div><div class="c">{esc(c)}</div></div>' for l, v, c in items
    )
    return f'<div class="kpis">{cards}</div>'


def panel(title: str, subtitle: str = "", body: str = "") -> str:
    sub = f'<div class="s">{esc(subtitle)}</div>' if subtitle else ""
    inner = f'<div class="body">{body}</div>' if body else ""
    return f'<div class="panel"><div class="t">{esc(title)}</div>{sub}{inner}</div>'


def section(title: str, sub: str = "") -> str:
    return f'<div class="sect">{esc(title)}</div>' + (f'<div class="sect-sub">{esc(sub)}</div>' if sub else "")


def chip(text: str, kind: str = "info", small: bool = False) -> str:
    return f'<span class="chip {kind}{" sm" if small else ""}"><i></i>{esc(text)}</span>'


def status_chip(status: str, small: bool = False) -> str:
    return chip(status, STATUS_CLS[status], small)


def note(text: str, kind: str = "") -> str:
    return f'<div class="note {kind}">{esc(text)}</div>'


def empty_state(title: str, text: str) -> str:
    return f'<div class="empty"><b>{esc(title)}</b>{esc(text)}</div>'


def activity_html(items: list[dict], limit: int = 8) -> str:
    if not items:
        return '<div class="s" style="color:var(--muted);font-size:1rem">Nothing yet. Upload evidence to begin.</div>'
    lis = "".join(f'<li><time>{esc(i["ts"])}</time><span>{esc(i["text"])}</span></li>' for i in items[:limit])
    return f'<ul class="act">{lis}</ul>'


def domain_bars(assessments: list[Assessment]) -> str:
    order: dict[str, list[Assessment]] = {}
    for a in assessments:
        order.setdefault(a.domain, []).append(a)
    rows = []
    weights = {"Met": 1.0, "Partially met": 0.5, "Not met": 0.0, "No evidence": 0.0}
    for dom, items in order.items():
        n = len(items)
        counts = Counter(a.status for a in items)
        segs = "".join(
            f'<span class="seg-{STATUS_CLS[s]}" style="width:{counts[s] / n * 100:.2f}%" title="{esc(s)}: {counts[s]}"></span>'
            for s in STATUS_ORDER if counts[s]
        )
        pct = round(sum(weights[a.status] for a in items) / n * 100)
        rows.append(f'<div class="dom"><span>{esc(dom.replace(" controls", ""))}</span><div class="bar">{segs}</div><span class="pct">{pct}%</span></div>')
    legend = "".join(f'<span><i class="seg-{STATUS_CLS[s]}"></i>{s}</span>' for s in STATUS_ORDER)
    return "".join(rows) + f'<div class="legend">{legend}</div>'


def heatmap_html(risks: list[RiskItem]) -> str:
    """5x5 grid: impact down the side (5 at the top), likelihood along the bottom."""
    counts = Counter((r.likelihood, r.impact) for r in risks)
    out = ['<div class="heat">']
    for impact in range(5, 0, -1):
        out.append(f'<div class="ax">{impact}</div>')
        for likelihood in range(1, 6):
            n = counts.get((likelihood, impact), 0)
            lvl = level_for(likelihood * impact)
            out.append(f'<div class="hc h-{lvl}{"" if n else " void"}" title="Likelihood {likelihood}, impact {impact}: {lvl}">{n or ""}</div>')
    out.append("<span></span>" + "".join(f'<div class="ax">{i}</div>' for i in range(1, 6)) + "</div>")
    out.append('<div class="ax" style="max-width:27.5rem;margin-top:.5rem;font-size:.86rem;color:var(--muted)">Rows: impact, 5 at the top. Columns: likelihood, 1 to 5.</div>')
    return "".join(out)


def assessment_card(a: Assessment, c: Optional[Control] = None) -> str:
    chips = status_chip(a.status)
    rs = {"Approved": "ok", "Rejected": "bad", "Draft": "info"}[a.review_status]
    chips += chip({"Approved": "Approved", "Rejected": "Rejected", "Draft": "Draft: needs review"}[a.review_status], rs)
    if a.citation_verified:
        chips += chip("Quote verified", "ok", small=True)
    if a.ai_status and a.ai_status != a.status:
        chips += chip(f"AI said {a.ai_status}", "none", small=True)
    quote = ""
    if a.quote:
        where = esc(a.source_doc) + (f", page {a.page}" if a.page else "")
        quote = f'<blockquote><mark>{esc(a.quote)}</mark></blockquote><div class="src"><b>Found word for word</b> in {where}</div>'
    why = f'<div class="why">{esc(a.reasoning)}</div>' if a.reasoning else ""
    gap = f'<div class="gap"><b>Still needed:</b> {esc(a.missing)}</div>' if a.missing else ""
    flag = f'<div class="flag">{esc(a.note)}</div>' if a.note else ""
    rev = ""
    if a.review_status != "Draft" and a.reviewer:
        rev = f'<div class="rev">{esc(a.review_status)} by {esc(a.reviewer)}' + (f' on {esc(a.reviewed_at)}' if a.reviewed_at else "") + (f'. Note: {esc(a.reviewer_note)}' if a.reviewer_note else "") + "</div>"
    dm = f'<div class="dm">{esc(a.domain)}</div>'
    return one_line(
        f'<div class="acard"><div class="top"><div><span class="id">{esc(a.control_id)}</span><span class="ttl">{esc(a.title)}</span></div>'
        f'<div class="chips">{chips}</div></div>{dm}{quote}{why}{gap}{flag}{rev}</div>'
    )


# ------------------------------------------------------------------ workflow map
def workflow_map_html(state: dict) -> str:
    """Self-contained HTML (own CSS) for st.components.v1.html. `state` carries live pipeline counts."""
    docs, chunks = state.get("docs", 0), state.get("chunks", 0)
    total, assessed = state.get("controls", 0), state.get("assessed", 0)
    risks, approved = state.get("risks", 0), state.get("approved", 0)
    indexed = state.get("indexed", False)

    nodes = [  # cx, cy, w, label, colour, step, sublabel, done
        (95, 215, 172, "Evidence", "#8B7CFF", "01 / INGEST", f"{docs} file{'s' if docs != 1 else ''} · PDF DOCX TXT", docs > 0),
        (315, 215, 172, "AI Extractor", "#8B7CFF", "02 / UNDERSTAND", f"{chunks} passages" if indexed else "Parse & index", indexed),
        (540, 215, 172, "GRC Core", "#8B7CFF", "03 / ORCHESTRATE", f"{assessed} of {total} assessed", assessed > 0),
        (780, 90, 172, "Control Map", "#4FD1C5", "", f"{state.get('framework', '')} · {total} controls".lstrip(" ·"), total > 0),
        (780, 340, 172, "Risk Agent", "#4FD1C5", "", f"{risks} open risk{'s' if risks != 1 else ''}", risks > 0),
        (1010, 215, 140, "Review", "#5EE6A4", "04 / HUMAN GATE", f"{approved} approved", approved > 0),
    ]
    paths = [
        ("M181 215 H229", "#8B7CFF"), ("M401 215 H454", "#8B7CFF"),
        ("M626 215 C665 215 655 90 694 90", "#4FD1C5"), ("M626 215 C665 215 655 340 694 340", "#4FD1C5"),
        ("M866 90 C905 90 905 205 940 205", "#5EE6A4"), ("M866 340 C905 340 905 225 940 225", "#5EE6A4"),
    ]
    # The viewBox is 1/UI_SCALE wider than the drawing (1100), centred: the map renders UI_SCALE times its old size.
    pad = 1100 * (1 / UI_SCALE - 1) / 2
    svg = [f'<svg viewBox="{-pad:g} 0 {1100 + 2 * pad:g} 420" role="img" aria-label="Workflow: evidence, AI extractor, GRC core, control map and risk agent, human review">']
    for d, col in paths:
        svg.append(f'<path d="{d}" fill="none" stroke="{col}" stroke-opacity=".55" stroke-width="2.4" stroke-dasharray="8 8" class="flow"/>')
    for i, (d, col) in enumerate(paths):
        svg.append(f'<circle r="5.5" fill="{col}" class="pulse"><animateMotion dur="{2.6 + (i % 3) * .5:.1f}s" begin="{i * .35:.2f}s" repeatCount="indefinite" path="{d}"/></circle>')
    for cx, cy, w, label, col, step, sub, done in nodes:
        x, y = cx - w / 2, cy - 34
        op = "1" if done else ".5"
        glow = f' style="filter:drop-shadow(0 0 12px {col}99)"' if done else ""
        if step:
            svg.append(f'<text x="{cx}" y="{cy - 52}" text-anchor="middle" class="step" fill="{col}">{esc(step)}</text>')
        svg.append(f'<g{glow}><rect x="{x}" y="{y}" width="{w}" height="68" rx="34" fill="#111A3E" stroke="{col}" stroke-opacity="{op}" stroke-width="2.4"/></g>')
        svg.append(f'<circle cx="{x + 24}" cy="{cy}" r="5.5" fill="{col if done else "none"}" stroke="{col}" stroke-opacity="{op}" stroke-width="2"/>')
        svg.append(f'<text x="{cx + 14}" y="{cy + 7.5}" text-anchor="middle" class="lbl">{esc(label)}</text>')
        svg.append(f'<text x="{cx}" y="{cy + 62}" text-anchor="middle" class="sub">{esc(sub)}</text>')
    svg.append("</svg>")
    items = []  # the same pipeline as a vertical list, shown instead of the diagram on narrow screens
    for _cx, _cy, _w, label, col, step, sub, done in nodes:
        branch = ' class="br"' if label in ("Control Map", "Risk Agent") else ""
        tag = f"<small>{esc(step)}</small>" if step else ""
        items.append(f'<li{branch} style="--c:{col};--o:{1 if done else .45}"><b>{esc(label)}</b>{tag}<span>{esc(sub)}</span></li>')
    stack = f"<ol class='stack'>{''.join(items)}</ol>"
    legend = (
        '<div class="lg"><span><i style="background:#8B7CFF"></i>AI processing path</span>'
        '<span><i style="background:#4FD1C5"></i>Framework mapping</span><span><i style="background:#5EE6A4"></i>Human approval gate</span></div>'
    )
    css = (
        "html,body{margin:0;background:transparent;font-family:-apple-system,'Segoe UI',Inter,Roboto,Helvetica,Arial,sans-serif;color:#EEF1FF}"
        # narrow screens (a phone): the diagram is swapped for a vertical list, so text stays readable and nothing scrolls sideways
        ".stack{display:none;list-style:none;margin:0;padding:6px 4px 0}.stack li{position:relative;margin:0 0 0 8px;padding:8px 0 10px 18px;border-left:2px solid #232C5E}"
        ".stack li.br{margin-left:30px}.stack li::before{content:'';position:absolute;left:-7px;top:12px;width:12px;height:12px;border-radius:50%;background:var(--c);opacity:var(--o)}"
        ".stack b{display:block;font-size:14px;color:#fff}.stack small{display:block;font-size:10.5px;font-weight:700;letter-spacing:.14em;color:var(--c);margin-top:1px}"
        ".stack span{display:block;font-size:12px;color:#8E98C4;margin-top:2px}@media (max-width:759px){.wrap{display:none}.stack{display:block}}"
        "svg{width:100%;height:auto;display:block}.lbl{font-size:20.5px;font-weight:700;fill:#fff}.sub{font-size:17px;fill:#8E98C4}"
        ".step{font-size:14.5px;font-weight:700;letter-spacing:.14em}.lg{display:flex;gap:18px;flex-wrap:wrap;padding:4px 12px 10px;font-size:11.5px;color:#C4CBEA}"
        ".lg span{display:inline-flex;align-items:center;gap:7px}.lg i{width:8px;height:8px;border-radius:50%;display:inline-block}"
        "@media (prefers-reduced-motion:reduce){.pulse{display:none}}"
    )
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{css}</style></head><body><div class='wrap'>{''.join(svg)}</div>{stack}{legend}</body></html>"
