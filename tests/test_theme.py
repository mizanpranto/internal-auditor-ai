from internal_auditor import theme as T
from internal_auditor.schemas import Assessment


def card(**kw):
    base = dict(control_id="5.2", domain="Organizational controls", title="Roles", status="Met")
    return T.assessment_card(Assessment(**{**base, **kw}))


def test_multiline_quote_keeps_its_word_breaks():
    html = card(quote="Roles and responsibilities\n\nThe CISO is responsible for the ISMS.", citation_verified=True,
                source_doc="policy.md")
    assert "responsibilities The CISO" in html and "responsibilitiesThe" not in html


def test_card_html_has_no_newlines_or_blank_lines():
    html = card(reasoning="Line one.\n\n    indented code?\n\nLine two.", missing="a\nb", note="x\r\ny")
    assert "\n" not in html and "\r" not in html


def test_untrusted_text_cannot_inject_markup():
    evil = '<script>alert(1)</script><img src=x onerror=alert(2)> "quoted" & $5 $$x$$'
    html = card(quote=evil, reasoning=evil, missing=evil, title=evil, reviewer_note=evil)
    assert "<script" not in html and "<img" not in html
    assert "&lt;script&gt;" in html and "&amp;" in html and "&quot;" in html
    assert "$" not in html  # would otherwise be picked up as maths by the markdown renderer


def test_esc_handles_none_and_numbers():
    assert T.esc(None) == "" and T.esc(42) == "42" and T.esc("  a \t b \n") == "a b"


# ----------------------------------------------------------------- name
def test_app_name_is_shown_in_brand_and_header():
    assert T.APP_NAME == "Internal Auditor"
    brand, header = T.brand_html(), T.page_header("Overview", "x")
    assert "Internal Auditor" in brand and "Internal Auditor / Intelligence workspace" in header
    assert "Aether" not in brand + header + T.css_block()


# ------------------------------------------------------------ scaling
def test_scale_px_converts_to_rem_so_everything_is_20_percent_smaller():
    out = T.scale_px("a{padding:16px 32px;width:160px}", scale=0.8)
    assert "padding:1.000rem 2.000rem" in out and "width:10.000rem" in out
    # 1rem is 12.8px once the root is 80%: 16px -> 12.8px, i.e. exactly 20% smaller
    assert 1.0 * 16 * 0.8 == 12.8


def test_scale_px_keeps_media_queries_hairlines_and_floors_font_sizes():
    css = "@media (max-width: 820px) { .a{ font-size: 20px; border: 1px solid red; outline: 2px solid; } } .b{ font-size: 12px; }"
    out = T.scale_px(css, scale=0.8, min_font_px=11)
    assert "@media (max-width: 820px)" in out          # screen widths are never scaled
    assert "1px solid" in out and "2px solid" in out    # hairlines stay crisp
    assert "font-size: 1.250rem" in out                 # 20px * 0.8 = 16px = 1.25rem
    assert "font-size: 0.859rem" in out                 # 12px * 0.8 = 9.6px, floored to 11px = 0.859rem


def test_scale_px_handles_decimals_negatives_and_clamp():
    out = T.scale_px("a{margin:0 -16px;font-size:clamp(30px, 3.6vw, 46px);top:.5px;gap:12.5px}", scale=0.8)
    assert "-1.000rem" in out and "clamp(1.875rem, 3.6vw, 2.875rem)" in out and ".5px" in out and "0.781rem" in out


def test_css_block_sets_root_size_and_leaves_no_big_px_outside_media_queries():
    import re
    css = T.css_block()
    assert css.startswith("<style>html { font-size: 80% !important; }")
    outside = re.sub(r"@media[^{]*", "", css)
    leftovers = [m for m in re.findall(r"(?<![\w.#])(\d*\.?\d+)px", outside) if float(m) > 2]
    assert leftovers == ["1500"]                         # only the unscaled page max-width remains
    assert "\n\n" not in css                             # blank lines would break Streamlit's markdown parser


# ---------------------------------------------------------- responsive
def test_every_breakpoint_exists_and_columns_stack_on_tablet():
    css = T.css_block()
    for width in ("1100px", "820px", "480px"):
        assert f"@media (max-width: {width})" in css
    tablet = css.split("@media (max-width: 820px)")[1].split("@media (max-width: 480px)")[0]
    assert "stHorizontalBlock" in tablet and "flex-wrap: wrap" in tablet and "min-width: 100%" in tablet


def test_workflow_map_scales_with_the_app_and_becomes_a_list_on_phones():
    doc = T.workflow_map_html({"docs": 3, "chunks": 20, "indexed": True, "controls": 93, "assessed": 10,
                               "risks": 4, "approved": 7, "framework": "ISO 27001"})
    assert 'viewBox="-137.5 0 1375 420"' in doc          # 1100 / 0.8 wide: the drawing renders 20% smaller
    assert "@media (max-width:759px){.wrap{display:none}.stack{display:block}}" in doc
    stack = doc.split("<ol class='stack'>")[1].split("</ol>")[0]
    assert stack.count("<li") == 6 and stack.count('class="br"') == 2   # six stages, two of them branches
    for text in ("Evidence", "AI Extractor", "GRC Core", "Control Map", "Risk Agent", "Review", "3 files", "7 approved", "4 open risks"):
        assert text in stack
    assert "ISO 27001 · 93 controls" in doc and doc.count("<svg") == 1
