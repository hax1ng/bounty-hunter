"""Dense dark workspace chrome — Burp / Binary Ninja, not a marketing site."""

from __future__ import annotations

from nicegui import ui

CSS = """
:root {
  --bh-bg: #121416;
  --bh-panel: #191c1f;
  --bh-panel-2: #22262a;
  --bh-border: #353b40;
  --bh-text: #d2d7da;
  --bh-dim: #818a91;
  --bh-accent: #e28a36;
  --bh-accent-2: #f1a34f;
  --bh-danger: #d34a43;
  --bh-ok: #52b788;
  --bh-header-h: 28px;
  --bh-toolbar-h: 32px;
  --bh-footer-h: 22px;
}
body, .q-page, #app, .nicegui-content {
  background: var(--bh-bg) !important;
  color: var(--bh-text);
  font-size: 12px;
  overflow: hidden;
}
.nicegui-content {
  padding: 0 !important;
  gap: 0 !important;
}
.bh-header {
  min-height: var(--bh-header-h) !important;
  height: var(--bh-header-h) !important;
  background: #272b2f !important;
  border-bottom: 1px solid var(--bh-border);
  padding: 0 4px !important;
  width: 100%;
}
.bh-header > .q-btn, .bh-toolbar > .q-btn { flex: 0 0 auto; }
.bh-header .bh-title {
  position: absolute;
  right: 8px;
  max-width: 42vw;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.bh-toolbar {
  min-height: var(--bh-toolbar-h);
  height: var(--bh-toolbar-h);
  background: #1f2326;
  border-bottom: 1px solid var(--bh-border);
  padding: 0 6px;
}
.bh-footer {
  min-height: var(--bh-footer-h) !important;
  height: var(--bh-footer-h) !important;
  background: #17191b !important;
  border-top: 1px solid var(--bh-border);
  padding: 0 8px !important;
  font-size: 11px;
  color: var(--bh-dim);
}
.bh-menu-btn {
  font-size: 12px !important;
  min-height: 24px !important;
  padding: 0 8px !important;
  text-transform: none !important;
}
.bh-body {
  height: calc(100vh - var(--bh-header-h) - var(--bh-toolbar-h) - var(--bh-footer-h));
  width: 100%;
  overflow: hidden;
}
.bh-pane {
  background: var(--bh-panel);
  height: 100%;
  overflow: auto;
}
.bh-pane-head {
  background: var(--bh-panel-2);
  border-bottom: 1px solid var(--bh-border);
  color: var(--bh-dim);
  font-size: 11px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  padding: 5px 8px;
  font-weight: 600;
}
.bh-log {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
  line-height: 1.35;
  background: #0e1011 !important;
  color: #c8c8c8;
}
.bh-title {
  font-size: 12px;
  color: var(--bh-text);
}
.bh-title-dirty { color: var(--bh-accent-2); }
.bh-accent { color: var(--bh-accent); }
.bh-ok { color: var(--bh-ok); }
.bh-bad { color: var(--bh-danger); }
.bh-dim { color: var(--bh-dim); }
.q-tree { font-size: 12px; }
.q-tree__node-header { padding: 2px 3px; min-height: 23px; }
.q-tab { text-transform: none; min-height: 28px; font-size: 11.5px; padding: 0 10px; }
.q-splitter__separator { background: var(--bh-border); }
.q-field { font-size: 12px; }
.q-field--dense .q-field__control, .q-field--dense .q-field__marginal { height: 34px; }
.q-textarea.q-field--dense .q-field__control { height: auto; }
.q-menu { background: #24282b !important; border: 1px solid var(--bh-border); }
.q-item { min-height: 28px; font-size: 12px; }
.q-table th { background: #252a2e; color: #adb5ba; font-size: 10px; text-transform: uppercase; }
.q-table td { font-size: 11.5px; height: 29px; }
.q-table tbody td:first-child, .bh-mono, .bh-mono-input input, .bh-mono-input textarea {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.bh-chat-msg { white-space: pre-wrap; font-size: 12.5px; }
.bh-chat-user { color: var(--bh-accent-2); }
.bh-chat-asst { color: #d0d0d0; }
.bh-kbd {
  border: 1px solid var(--bh-border);
  padding: 0 4px;
  font-size: 10px;
  color: var(--bh-dim);
  margin-left: 2px;
}
.bh-kill { margin-left: 2px; font-weight: 700; min-width: 68px; }
.bh-model-chip { font-size: 10px; padding: 2px 7px; }
.bh-path { color: var(--bh-dim); max-width: 35vw; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bh-main-tabs, .bh-log-tabs { background: #22262a; border-bottom: 1px solid var(--bh-border); }
.bh-main-panels, .bh-log-panels { background: var(--bh-panel) !important; }
.bh-main-panels .q-tab-panel { padding: 10px 12px; }
.bh-log-panels .q-tab-panel { height: 100%; }
.bh-log-tabs .q-tab { min-height: 24px; }
.bh-section-title { font-size: 13px; font-weight: 650; letter-spacing: .06em; color: #e4e8ea; }
.bh-subhead { color: #9da6ab; font-size: 10px; font-weight: 650; letter-spacing: .09em; margin-top: 4px; }
.bh-stat-strip { background: #15181a; border: 1px solid var(--bh-border); margin: 5px 0 8px; }
.bh-stat { padding: 8px 13px; min-width: 105px; flex: 1 1 0; border-right: 1px solid var(--bh-border); }
.bh-stat-value { font-size: 16px; color: #e3e7e9; }
.bh-stat-label { color: var(--bh-dim); font-size: 9px; letter-spacing: .08em; }
.bh-kv-key { color: var(--bh-dim); width: 105px; flex: 0 0 105px; }
.bh-event-line { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 10.5px; color: #aab1b5; }
.bh-table { border: 1px solid var(--bh-border); }
.bh-list-row { min-height: 32px; padding: 2px 6px; border-bottom: 1px solid #2b3034; }
.bh-panel-note { color: var(--bh-dim); font-size: 10.5px; padding: 4px 8px; border-bottom: 1px solid var(--bh-border); }
.bh-message { max-width: 94%; padding: 5px 7px; border: 1px solid #33393e; background: #202428; }
.bh-message-user { align-self: flex-end; border-color: #69451f; background: #29231d; }
.bh-message-who { color: var(--bh-dim); font-size: 8.5px; letter-spacing: .09em; }
.bh-chat-input { padding-bottom: 2px; }
.bh-roster-row { min-height: 34px; padding: 3px 7px; border-bottom: 1px solid #2b3034; }
.bh-roster-name { font-size: 11px; }
.bh-roster-blurb { color: var(--bh-dim); font-size: 9px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 190px; }
.bh-report-preview { background: #111315; border: 1px solid var(--bh-border); padding: 12px 16px; margin-top: 6px; }
.bh-dashboard-actions { background: #171a1c; border: 1px solid var(--bh-border); padding: 7px; }
.bh-setup-preview { background: #111315; border: 1px solid var(--bh-border); padding: 9px; min-height: 72px; }
.bh-report-preview h1, .bh-report-preview h2 { color: #dfe4e6; }
.bh-console { background: #101213; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 11px; }
.bh-tab-close { margin-left: 4px; }
.bh-tree .q-tree__node-header-content { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
"""


def apply_theme() -> None:
    ui.dark_mode().enable()
    ui.colors(
        primary="#e67e22",
        secondary="#2c3e50",
        accent="#f39c12",
        dark="#161616",
        dark_page="#161616",
        positive="#27ae60",
        negative="#c0392b",
        info="#4aa3df",
        warning="#f1c40f",
    )
    ui.add_css(CSS)
    ui.query(".nicegui-content").classes("p-0 gap-0")
    ui.query("body").style("overflow: hidden")
