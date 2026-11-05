/*
 * Coffee drinks card for De'Longhi My Coffee Lounge.
 *
 * Shipped with the integration and loaded on every dashboard. Add it from
 * "Add card → Coffee drinks", or in YAML:
 *   type: custom:delonghi-drinks-card
 *   title: Coffee              # optional
 *   size: medium               # small (default), medium or large
 *   drinks: [8, 1, 33]         # optional: only these drinks, in this order
 *   entry_id: …                # only with more than one machine
 *
 * The drink list comes from Home Assistant (delonghi_daedalus/drinks). Drinks
 * and settings go through the integration's entities, so Home Assistant's
 * safety checks apply.
 */

const TAG = "delonghi-drinks-card";
const EDITOR_TAG = "delonghi-drinks-card-editor";
const REFRESH_MS = 10 * 60 * 1000;
const SKELETON_DELAY_MS = 300;
const NOTICE_MS = 4000;
const CHIPS_FROM = 9; // filter chips only help with this many drinks or more
const SIZES = { small: "Small", medium: "Medium", large: "Large" };

const FILTERS = [
  { key: "all", label: "All", test: () => true },
  { key: "coffee", label: "Coffee", test: (g) => g.has("coffee") && !g.has("milk") && !g.has("cold") },
  { key: "milk", label: "With milk", test: (g) => g.has("milk") && !g.has("cold") },
  { key: "cold", label: "Cold", test: (g) => g.has("cold") },
  { key: "other", label: "Tea & water", test: (g) => !g.has("coffee") && !g.has("milk") && !g.has("cold") },
];

// Settings behind "More", in order: [entity key, label].
const MORE_SETTINGS = [
  ["coffee_temperature", "Coffee temperature"],
  ["auto_off_timer", "Turns off after"],
  ["water_hardness", "Water hardness"],
  ["sound", "Sound"],
  ["energy_saving", "Energy saving"],
  ["cup_warmer", "Cup warmer"],
];

const haptic = (kind) => window.dispatchEvent(new CustomEvent("haptic", { detail: kind }));

const store = {
  get(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (_) {
      return null;
    }
  },
  set(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (_) {
      /* private mode: not remembered, still works */
    }
  },
};

const loadMachines = (hass) => hass.callWS({ type: "delonghi_daedalus/drinks" }).then((r) => r.machines);

/** The drinks a card shows: chosen ones in their order, or all; only those whose button is on. */
function chosenDrinks(machine, config, states) {
  const available = machine.drinks.filter((d) => states[d.entity_id]);
  if (!Array.isArray(config.drinks)) return available;
  const byId = new Map(available.map((d) => [d.id, d]));
  return config.drinks.map((id) => byId.get(Number(id))).filter(Boolean);
}

function element(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key.startsWith("aria-") || key === "role" || key === "for") node.setAttribute(key, value);
    else node[key] = value;
  }
  node.append(...children.filter((c) => c !== null && c !== undefined && c !== false));
  return node;
}

const SHARED_STYLE = `
  :host {
    --dl-spring: cubic-bezier(0.2, 0.9, 0.3, 1);
    --dl-accent: var(--primary-color);
    --dl-on-accent: var(--text-primary-color, #fff);
    --dl-text: var(--primary-text-color);
    --dl-text-2: var(--secondary-text-color);
    --dl-fill: var(--secondary-background-color, rgba(127, 127, 127, 0.08));
    --dl-hairline: var(--divider-color, rgba(127, 127, 127, 0.2));
  }
  button { font: inherit; color: inherit; -webkit-tap-highlight-color: transparent; }
  button:focus-visible, select:focus-visible, input:focus-visible { outline: 2px solid var(--dl-accent); outline-offset: 2px; }
  .toggle { position: relative; flex: none; width: 51px; height: 31px; padding: 0; border: none; border-radius: 16px;
            background: var(--dl-hairline); cursor: pointer; transition: background-color 0.25s ease; }
  .toggle::after { content: ""; position: absolute; top: 2px; left: 2px; width: 27px; height: 27px; border-radius: 50%;
            background: #fff; box-shadow: 0 2px 4px rgba(0,0,0,0.2), 0 0 1px rgba(0,0,0,0.15);
            transition: transform 0.3s var(--dl-spring); }
  .toggle[aria-checked="true"] { background: var(--dl-accent); }
  .toggle[aria-checked="true"]::after { transform: translateX(20px); }
  .toggle:disabled { opacity: 0.4; cursor: default; }
  .toggle::before { content: ""; position: absolute; inset: -7px -4px; }
  select.choice { min-height: 36px; max-width: 100%; padding: 0 30px 0 12px; border: 0.5px solid var(--dl-hairline);
            border-radius: 10px; background-color: var(--dl-fill); color: var(--dl-text); font: inherit; font-size: 15px;
            appearance: none; -webkit-appearance: none; cursor: pointer; text-overflow: ellipsis;
            background-image: linear-gradient(45deg, transparent 50%, currentColor 50%), linear-gradient(135deg, currentColor 50%, transparent 50%);
            background-position: calc(100% - 16px) 50%, calc(100% - 11px) 50%; background-size: 5px 5px; background-repeat: no-repeat; }
  select.choice:disabled { opacity: 0.4; cursor: default; }
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation: none !important; transition: none !important; }
  }
`;

const STYLE = `
  :host { --dl-tile: 104px; --dl-name: 13px; --dl-name-line: 17px; }
  :host([size="medium"]) { --dl-tile: 148px; --dl-name: 15px; --dl-name-line: 20px; }
  :host([size="large"]) { --dl-tile: 210px; --dl-name: 17px; --dl-name-line: 22px; }
  ha-card { padding: 16px 16px 12px; overflow: hidden; }
  header { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; min-height: 28px; }
  h2 { margin: 0; font-size: 20px; line-height: 28px; font-weight: 600; letter-spacing: -0.01em;
       color: var(--dl-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .status { flex: none; font-size: 14px; color: var(--dl-text-2); font-variant-numeric: tabular-nums; }
  .progress { height: 3px; margin: 8px 0 0; border-radius: 2px; background: var(--dl-fill); overflow: hidden;
              opacity: 0; transition: opacity 0.25s ease; }
  .progress.on { opacity: 1; }
  .progress > div { height: 100%; width: 0; background: var(--dl-accent); border-radius: inherit;
                    transition: width 0.4s var(--dl-spring); }

  .controls { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; margin-top: 12px; }
  .control { display: flex; align-items: center; gap: 10px; min-height: 44px; font-size: 15px; color: var(--dl-text); }
  .control label { color: var(--dl-text-2); }
  .more { margin-left: auto; min-height: 36px; padding: 0 14px; border: none; border-radius: 18px; background: var(--dl-fill);
          font-size: 15px; font-weight: 500; cursor: pointer; transition: transform 0.2s var(--dl-spring); }
  .more:active { transform: scale(0.96); }
  .notice { min-height: 0; font-size: 13px; line-height: 18px; color: var(--error-color, #d93025);
            transition: opacity 0.25s ease; }
  .notice:empty { opacity: 0; }

  .chips { display: flex; gap: 8px; margin: 12px -16px 4px; padding: 0 16px 4px; overflow-x: auto;
           scrollbar-width: none; scroll-padding: 16px; }
  .chips::-webkit-scrollbar { display: none; }
  .chip { flex: none; min-height: 32px; padding: 0 14px; border-radius: 16px; border: 0.5px solid var(--dl-hairline);
          background: transparent; font-size: 14px; font-weight: 500;
          cursor: pointer; transition: background-color 0.2s ease, color 0.2s ease, transform 0.2s var(--dl-spring); }
  .chip[aria-pressed="true"] { background: var(--dl-accent); border-color: transparent; color: var(--dl-on-accent); }
  .chip:active { transform: scale(0.96); }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(var(--dl-tile), 1fr)); gap: 8px; margin-top: 12px; }
  .tile { display: flex; flex-direction: column; min-width: 0; padding: 0 0 8px; border: none; border-radius: 12px;
          background: transparent; text-align: center; cursor: pointer;
          transition: transform 0.25s var(--dl-spring), background-color 0.2s ease; }
  .tile:hover { background: var(--dl-fill); }
  .tile:active { transform: scale(0.96); }
  .photo { position: relative; aspect-ratio: 412 / 452; border-radius: 12px; background: var(--dl-fill);
           display: grid; place-items: center; overflow: hidden; }
  .photo img { width: 100%; height: 100%; object-fit: contain; opacity: 0; transition: opacity 0.3s ease; }
  .photo img.loaded { opacity: 1; }
  .photo ha-icon { --mdc-icon-size: 32px; color: var(--dl-text-2); }
  .name { margin: 6px 4px 0; font-size: var(--dl-name); line-height: var(--dl-name-line); font-weight: 500;
          display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere; }
  .skeleton .photo { animation: pulse 1.4s ease-in-out infinite; }
  .skeleton .name { height: 12px; width: 70%; margin: 8px auto 0; border-radius: 6px; background: var(--dl-fill); }
  @keyframes pulse { 50% { opacity: 0.55; } }
  .message { padding: 24px 8px 16px; text-align: center; color: var(--dl-text-2); font-size: 14px; line-height: 20px; }
  .message button { margin-top: 12px; }

  dialog { width: min(380px, calc(100vw - 32px)); max-height: calc(100dvh - 32px); padding: 12px; border: none;
           border-radius: 20px; background: var(--card-background-color, var(--ha-card-background, #fff));
           color: var(--dl-text); box-shadow: 0 1px 2px rgba(0,0,0,0.08), 0 12px 40px rgba(0,0,0,0.24); }
  dialog[open] { animation: rise 0.32s var(--dl-spring); }
  dialog::backdrop { background: rgba(0, 0, 0, 0.32); animation: fade 0.2s ease; }
  @keyframes rise { from { opacity: 0; transform: translateY(12px) scale(0.97); } }
  @keyframes fade { from { opacity: 0; } }
  dialog .photo { border-radius: 8px; }
  dialog h3 { margin: 16px 4px 0; font-size: 22px; line-height: 28px; font-weight: 600; letter-spacing: -0.015em; }
  dialog.settings h3 { margin: 4px 48px 8px 4px; }
  dialog p { margin: 6px 4px 0; font-size: 15px; line-height: 21px; color: var(--dl-text-2); }
  .rows { margin: 4px 0 0; border-radius: 12px; background: var(--dl-fill); }
  .row { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 52px; padding: 0 12px; }
  .row + .row { box-shadow: inset 0 0.5px 0 var(--dl-hairline); }
  .row label { font-size: 16px; }
  .actions { display: grid; gap: 8px; margin-top: 20px; }
  .primary, .secondary { min-height: 48px; padding: 0 16px; border-radius: 12px; border: none;
          font-size: 16px; font-weight: 600; cursor: pointer;
          transition: transform 0.2s var(--dl-spring), opacity 0.2s ease, background-color 0.2s ease; }
  .primary { background: var(--dl-accent); color: var(--dl-on-accent); }
  .secondary { background: var(--dl-fill); }
  .primary:active:not(:disabled), .secondary:active:not(:disabled) { transform: scale(0.97); }
  .primary:disabled { opacity: 0.4; cursor: default; }
  .note { min-height: 20px; margin: 10px 4px 2px; font-size: 13px; line-height: 18px; color: var(--dl-text-2); text-align: center; }
  .note.error { color: var(--error-color, #d93025); }
  .close { position: absolute; top: 18px; right: 18px; width: 32px; height: 32px; border-radius: 16px; border: none;
           display: grid; place-items: center; background: rgba(0,0,0,0.35); color: #fff; cursor: pointer;
           backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px); }
  dialog.settings .close { top: 14px; right: 14px; background: var(--dl-fill); color: var(--dl-text);
           backdrop-filter: none; -webkit-backdrop-filter: none; }
  .close::before { content: ""; position: absolute; inset: -6px; }
  .close ha-icon { --mdc-icon-size: 18px; }
  @media (prefers-reduced-motion: reduce) { dialog[open], dialog::backdrop { animation: none !important; } }
`;

class DelonghiDrinksCard extends HTMLElement {
  constructor() {
    super();
    this._root = this.attachShadow({ mode: "open" });
    this._machine = null;
    this._error = null;
    this._loading = false;
    this._filter = "all";
    this._sheet = null; // {kind: "drink", drink} or {kind: "settings"}
    this._busy = false;
    this._note = null; // message under the drink sheet's buttons: {text, error, clearWhenReady}
    this._pendingRender = false;
  }

  static getConfigElement() {
    return document.createElement(EDITOR_TAG);
  }

  static getStubConfig() {
    return {};
  }

  setConfig(config) {
    const next = { ...(config || {}) };
    if (next.size !== undefined && !SIZES[next.size]) throw new Error("size must be small, medium or large");
    if (next.drinks !== undefined && !Array.isArray(next.drinks)) throw new Error("drinks must be a list of drink numbers");
    this._config = next;
    this.setAttribute("size", next.size || "small");
    this._render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._load();
    else this._updateLive();
  }

  getCardSize() {
    return 6;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  connectedCallback() {
    this._timer = window.setInterval(() => this._load(), REFRESH_MS);
    this._onVisible = () => document.visibilityState === "visible" && this._load();
    document.addEventListener("visibilitychange", this._onVisible);
  }

  disconnectedCallback() {
    window.clearInterval(this._timer);
    document.removeEventListener("visibilitychange", this._onVisible);
  }

  async _load() {
    if (!this._hass || this._loading) return;
    this._loading = true;
    const skeleton = window.setTimeout(() => !this._machine && this._render(true), SKELETON_DELAY_MS);
    try {
      const machines = await loadMachines(this._hass);
      const wanted = this._config && this._config.entry_id;
      this._machine = (wanted ? machines.find((m) => m.entry_id === wanted) : machines[0]) || null;
      this._error = null;
      if (this._machine) {
        this._filter = store.get(`${TAG}:filter:${this._machine.entry_id}`) || "all";
      }
    } catch (err) {
      this._error = err;
    } finally {
      window.clearTimeout(skeleton);
      this._loading = false;
    }
    // Never redraw under an open sheet; catch up when it closes.
    if (this._sheet) this._pendingRender = true;
    else this._render();
  }

  /* ---- what the card shows ---- */

  _state(entityId) {
    return entityId ? this._hass.states[entityId] : undefined;
  }

  _control(key) {
    const m = this._machine;
    return m && m.controls ? this._state(m.controls[key]) : undefined;
  }

  _status() {
    const state = this._state(this._machine && this._machine.status_entity_id);
    if (!state) return "";
    if (state.state === "unavailable" || state.state === "unknown") return "Not reachable";
    const progress = this._progress();
    if (progress !== null) return `Making… ${progress}%`;
    return this._hass.formatEntityState ? this._hass.formatEntityState(state) : state.state;
  }

  _progress() {
    const state = this._state(this._machine && this._machine.progress_entity_id);
    const value = state ? Number(state.state) : NaN;
    return Number.isFinite(value) && value > 0 && value < 100 ? Math.round(value) : null;
  }

  /** Why a drink can't start now, or null when it can. */
  _blocker(drink) {
    const button = this._state(drink.entity_id);
    if (button && button.state !== "unavailable") return null;
    const power = this._control("power");
    const status = this._state(this._machine.status_entity_id);
    if (!status || status.state === "unavailable") return { text: "Home Assistant can’t reach the machine right now." };
    if (power && power.state === "off") return { text: "The machine is in standby.", turnOn: true };
    if (this._progress() !== null) return { text: "The machine is making a drink." };
    return { text: "The machine isn’t ready. Check it for a message, like an empty water tank." };
  }

  _optionLabel(state, option) {
    // The integration's own labels ("30 minutes", "Profile 2"), in Home Assistant's language.
    return this._hass.formatEntityState ? this._hass.formatEntityState(state, option) : option;
  }

  /** The value to show: what was just chosen, until the machine confirms it (or 5 s pass). */
  _shown(key, state) {
    const wish = this._wishes && this._wishes[key];
    if (wish && Date.now() < wish.until && (!state || state.state !== wish.value)) return wish.value;
    if (wish) delete this._wishes[key];
    return state ? state.state : "";
  }

  /* ---- drawing ---- */

  _render(skeleton = false) {
    if (!this._config) return;
    const title = this._config.title || (this._machine && this._machine.name) || "Coffee";
    this._root.innerHTML = `<style>${SHARED_STYLE}${STYLE}</style>
      <ha-card>
        <header><h2></h2><span class="status" aria-live="polite"></span></header>
        <div class="progress" aria-hidden="true"><div></div></div>
        <div class="body"></div>
      </ha-card>
      <dialog></dialog>`;
    this._root.querySelector("h2").textContent = title;
    const dialog = this._root.querySelector("dialog");
    dialog.addEventListener("click", (e) => e.target === dialog && dialog.close());
    dialog.addEventListener("close", () => {
      this._sheet = null;
      if (this._pendingRender) {
        this._pendingRender = false;
        this._render();
      }
    });
    const body = this._root.querySelector(".body");

    if (skeleton) {
      body.innerHTML = `<div class="grid" aria-hidden="true">${'<div class="tile skeleton"><div class="photo"></div><div class="name"></div></div>'.repeat(6)}</div>`;
      return;
    }
    if (this._error) {
      body.append(
        element("div", { class: "message" }, "Couldn’t load the drinks.", element("br"),
          element("button", { class: "secondary", text: "Try again", onclick: () => this._load() }))
      );
      return;
    }
    if (!this._hass || (!this._machine && this._loading)) return;
    if (!this._machine) {
      body.append(element("div", { class: "message", text: "No De’Longhi machine is set up in Home Assistant." }));
      return;
    }

    body.append(this._controlsRow(), element("div", { class: "notice", role: "status" }));

    const drinks = chosenDrinks(this._machine, this._config, this._hass.states);
    if (!drinks.length) {
      const text = Array.isArray(this._config.drinks)
        ? "None of this card’s drinks are available. Edit the card to choose drinks."
        : "No drinks yet. They appear once Home Assistant has read the machine’s drink list.";
      body.append(element("div", { class: "message", text }));
      this._updateLive();
      return;
    }

    const filters = drinks.length >= CHIPS_FROM
      ? FILTERS.filter((f) => f.key === "all" || drinks.some((d) => f.test(new Set(d.groups))))
      : [];
    if (!filters.some((f) => f.key === this._filter)) this._filter = "all";
    if (filters.length > 2) {
      const chips = element("div", { class: "chips", role: "group", "aria-label": "Drink types" });
      for (const f of filters) {
        chips.append(element("button", {
          class: "chip",
          text: f.label,
          "aria-pressed": String(f.key === this._filter),
          onclick: () => {
            haptic("selection");
            this._filter = f.key;
            store.set(`${TAG}:filter:${this._machine.entry_id}`, f.key);
            this._render();
          },
        }));
      }
      body.append(chips);
    }

    const grid = element("div", { class: "grid" });
    const test = FILTERS.find((f) => f.key === this._filter).test;
    for (const drink of drinks.filter((d) => this._filter === "all" || test(new Set(d.groups)))) {
      grid.append(this._tile(drink));
    }
    body.append(grid);
    this._updateLive();
  }

  _controlsRow() {
    const row = element("div", { class: "controls" });
    const controls = this._machine.controls || {};
    if (controls.power && this._state(controls.power)) {
      const toggle = element("button", {
        class: "toggle",
        role: "switch",
        id: "power",
        "aria-label": "Power",
        onclick: () => this._setSwitch("power", toggle.getAttribute("aria-checked") !== "true"),
      });
      row.append(element("div", { class: "control" }, element("label", { for: "power", text: "Power" }), toggle));
    }
    if (controls.brew_profile && this._state(controls.brew_profile)) {
      const select = element("select", {
        class: "choice",
        id: "profile",
        "aria-label": "Brew profile",
        onchange: () => this._select("brew_profile", select.value),
      });
      row.append(element("div", { class: "control" }, select));
    }
    if (MORE_SETTINGS.some(([key]) => this._control(key))) {
      row.append(element("button", { class: "more", text: "Settings", onclick: () => this._openSettings() }));
    }
    return row;
  }

  _fillSelect(select, state) {
    const options = (state && state.attributes.options) || [];
    const current = this._shown(select.dataset.key, state);
    if (select.dataset.options !== options.join("|")) {
      select.replaceChildren(...options.map((o) => element("option", { value: o, text: this._optionLabel(state, o) })));
      select.dataset.options = options.join("|");
    }
    const usable = state && options.includes(current);
    if (!usable && !select.querySelector('option[value=""]')) select.prepend(element("option", { value: "", text: "—" }));
    if (usable) select.querySelector('option[value=""]')?.remove();
    select.value = usable ? current : "";
    select.disabled = !usable || select.dataset.pending === "1";
  }

  _fillToggle(toggle, state) {
    const known = state && (state.state === "on" || state.state === "off");
    toggle.setAttribute("aria-checked", String(known && this._shown(toggle.dataset.key, state) === "on"));
    toggle.disabled = !known || toggle.dataset.pending === "1";
  }

  async _select(key, option) {
    const entityId = this._machine.controls[key];
    haptic("selection");
    await this._call(key, option, "select", "select_option", { entity_id: entityId, option });
  }

  async _setSwitch(key, on) {
    const entityId = this._machine.controls[key];
    haptic("light");
    await this._call(key, on ? "on" : "off", "switch", on ? "turn_on" : "turn_off", { entity_id: entityId });
  }

  async _call(key, value, domain, service, data) {
    const controls = [...this._root.querySelectorAll(`[data-key="${key}"]`)];
    controls.forEach((c) => (c.dataset.pending = "1"));
    this._wishes = { ...(this._wishes || {}), [key]: { value, until: Date.now() + 5000 } };
    this._updateLive();
    try {
      await this._hass.callService(domain, service, data);
    } catch (err) {
      delete this._wishes[key];
      haptic("failure");
      this._notice((err && err.message) || "The machine didn’t take the change. Try again.");
    } finally {
      controls.forEach((c) => delete c.dataset.pending);
      this._updateLive();
    }
  }

  _notice(text) {
    const target = this._sheet && this._sheet.kind === "settings"
      ? this._root.querySelector("dialog .note")
      : this._root.querySelector(".notice");
    if (!target) return;
    target.classList.add("error");
    target.textContent = text;
    window.clearTimeout(this._noticeTimer);
    this._noticeTimer = window.setTimeout(() => {
      target.textContent = "";
      target.classList.remove("error");
      if (this._sheet && this._sheet.kind === "settings") this._updateSettingsSheet();
    }, NOTICE_MS);
  }

  _photo(drink, eager = false) {
    const box = element("div", { class: "photo" });
    if (drink.photo) {
      const img = element("img", { alt: "", decoding: "async", loading: eager ? "eager" : "lazy" });
      img.addEventListener("load", () => img.classList.add("loaded"));
      img.addEventListener("error", () => img.replaceWith(this._icon(drink)));
      img.src = drink.photo;
      box.append(img);
    } else {
      box.append(this._icon(drink));
    }
    return box;
  }

  _icon(drink) {
    const icon = document.createElement("ha-icon");
    const state = this._state(drink.entity_id);
    icon.icon = (state && state.attributes.icon) || "mdi:coffee";
    return icon;
  }

  _tile(drink) {
    return element("button", {
      class: "tile",
      onclick: () => {
        haptic("light");
        this._openDrink(drink);
      },
    }, this._photo(drink), element("span", { class: "name", text: drink.name }));
  }

  _updateLive() {
    if (!this._machine || !this._root.querySelector(".status")) return;
    this._root.querySelector(".status").textContent = this._status();
    const progress = this._progress();
    const bar = this._root.querySelector(".progress");
    bar.classList.toggle("on", progress !== null);
    bar.firstElementChild.style.width = `${progress || 0}%`;
    const power = this._root.querySelector("#power");
    if (power) {
      power.dataset.key = "power";
      this._fillToggle(power, this._control("power"));
    }
    const profile = this._root.querySelector("#profile");
    if (profile) {
      profile.dataset.key = "brew_profile";
      this._fillSelect(profile, this._control("brew_profile"));
    }
    if (this._sheet && this._sheet.kind === "drink") this._updateDrinkSheet();
    if (this._sheet && this._sheet.kind === "settings") this._updateSettingsSheet();
  }

  /* ---- sheets ---- */

  _closeButton(dialog) {
    const close = element("button", { class: "close", "aria-label": "Close", onclick: () => dialog.close() });
    close.innerHTML = '<ha-icon icon="mdi:close"></ha-icon>';
    return close;
  }

  _openDrink(drink) {
    const dialog = this._root.querySelector("dialog");
    this._sheet = { kind: "drink", drink };
    this._busy = false;
    this._note = null;
    dialog.className = "";
    const title = element("h3", { id: "sheet-title", text: drink.name });
    dialog.setAttribute("aria-labelledby", "sheet-title");
    const make = element("button", { class: "primary", onclick: () => this._make(drink) });
    const turnOn = element("button", { class: "secondary", text: "Turn on", onclick: () => this._turnOn() });
    dialog.replaceChildren(
      this._photo(drink, true),
      this._closeButton(dialog),
      title,
      drink.description ? element("p", { text: drink.description }) : null,
      element("div", { class: "actions" }, make, turnOn),
      element("div", { class: "note", role: "status" })
    );
    this._updateDrinkSheet();
    dialog.showModal();
    make.focus();
  }

  _openSettings() {
    const dialog = this._root.querySelector("dialog");
    this._sheet = { kind: "settings" };
    dialog.className = "settings";
    dialog.setAttribute("aria-labelledby", "sheet-title");
    const rows = element("div", { class: "rows" });
    for (const [key, label] of MORE_SETTINGS) {
      const state = this._control(key);
      if (!state) continue; // switched off in Home Assistant
      const id = `setting-${key}`;
      let control;
      if (state.entity_id.startsWith("switch.")) {
        control = element("button", {
          class: "toggle", role: "switch", id, "aria-label": label,
          onclick: () => this._setSwitch(key, control.getAttribute("aria-checked") !== "true"),
        });
      } else {
        control = element("select", { class: "choice", id, onchange: () => this._select(key, control.value) });
      }
      control.dataset.key = key;
      rows.append(element("div", { class: "row" }, element("label", { for: id, text: label }), control));
    }
    dialog.replaceChildren(
      this._closeButton(dialog),
      element("h3", { id: "sheet-title", text: "Settings" }),
      rows,
      element("div", { class: "note", role: "status" })
    );
    this._updateSettingsSheet();
    dialog.showModal();
  }

  _updateSettingsSheet() {
    const dialog = this._root.querySelector("dialog");
    let anyUnknown = false;
    for (const control of dialog.querySelectorAll("[data-key]")) {
      const state = this._control(control.dataset.key);
      if (!state || state.state === "unavailable" || state.state === "unknown") anyUnknown = true;
      if (control.tagName === "SELECT") this._fillSelect(control, state);
      else this._fillToggle(control, state);
    }
    const note = dialog.querySelector(".note");
    if (note && !note.classList.contains("error")) {
      note.textContent = anyUnknown ? "Some settings show once the machine has reported them." : "";
    }
  }

  _setNote(text, error = false, clearWhenReady = false) {
    this._note = { text, error, clearWhenReady };
    this._updateDrinkSheet();
  }

  _updateDrinkSheet() {
    const dialog = this._root.querySelector("dialog");
    const drink = this._sheet && this._sheet.drink;
    if (!dialog || !drink) return;
    const make = dialog.querySelector(".primary");
    const turnOn = dialog.querySelector(".secondary");
    const note = dialog.querySelector(".note");
    const blocker = this._blocker(drink);
    if (!this._busy) make.textContent = `Make ${drink.name}`;
    make.disabled = this._busy || Boolean(blocker);
    turnOn.hidden = !(blocker && blocker.turnOn);
    if (this._note && this._note.clearWhenReady && !blocker) this._note = null;
    note.classList.toggle("error", Boolean(this._note && this._note.error));
    note.textContent = this._note ? this._note.text : blocker ? blocker.text : "Put a cup under the spout first.";
  }

  async _make(drink) {
    if (this._busy) return;
    this._busy = true;
    const make = this._root.querySelector("dialog .primary");
    make.textContent = "Starting…";
    this._setNote("Starting…");
    try {
      await this._hass.callService("button", "press", { entity_id: drink.entity_id });
      haptic("success");
      make.textContent = "Started";
      this._setNote(`Your ${drink.name} is on its way.`);
      window.setTimeout(() => this._root.querySelector("dialog").close(), 1200);
    } catch (err) {
      haptic("failure");
      this._busy = false;
      this._setNote((err && err.message) || "The machine didn’t start the drink. Try again.", true);
    }
  }

  async _turnOn() {
    const entityId = (this._machine.controls || {}).power;
    if (!entityId) return;
    this._setNote("Turning on… You can start the drink once the machine is ready.", false, true);
    try {
      await this._hass.callService("switch", "turn_on", { entity_id: entityId });
      haptic("light");
    } catch (err) {
      this._setNote((err && err.message) || "Couldn’t turn the machine on.", true);
    }
  }
}

/* ---- the card's editor ---- */

const EDITOR_STYLE = `
  :host { display: block; }
  .field { margin: 0 0 20px; }
  .field > label, .heading { display: block; margin: 0 0 8px; font-size: 14px; font-weight: 500; color: var(--dl-text); }
  input[type="text"] { box-sizing: border-box; width: 100%; min-height: 44px; padding: 0 12px; border-radius: 10px;
          border: 0.5px solid var(--dl-hairline); background: var(--dl-fill); color: var(--dl-text); font: inherit; font-size: 16px; }
  .segments { display: grid; grid-auto-flow: column; grid-auto-columns: 1fr; padding: 2px; border-radius: 10px; background: var(--dl-fill); }
  .segments button { min-height: 36px; border: none; border-radius: 8px; background: transparent; font-size: 14px;
          font-weight: 500; cursor: pointer; transition: background-color 0.2s ease, box-shadow 0.2s ease; }
  .segments button[aria-pressed="true"] { background: var(--card-background-color, #fff);
          box-shadow: 0 1px 3px rgba(0,0,0,0.12), 0 0 0 0.5px rgba(0,0,0,0.04); }
  .switch-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 44px; }
  .hint { margin: 4px 0 8px; font-size: 13px; line-height: 18px; color: var(--dl-text-2); }
  .list { border-radius: 12px; background: var(--dl-fill); overflow: hidden; }
  .item { display: flex; align-items: center; gap: 12px; min-height: 56px; padding: 0 4px 0 12px; }
  .item + .item { box-shadow: inset 0 0.5px 0 var(--dl-hairline); }
  .item.dragging { opacity: 0.5; }
  .item input { width: 22px; height: 22px; margin: 0; accent-color: var(--dl-accent); flex: none; }
  .thumb { width: 36px; height: 40px; flex: none; border-radius: 6px; object-fit: contain; }
  .item .label { flex: 1; min-width: 0; font-size: 15px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .move { width: 44px; height: 44px; flex: none; border: none; border-radius: 8px; background: transparent;
          color: var(--dl-text-2); cursor: pointer; }
  .move:disabled { opacity: 0.25; cursor: default; }
  .move ha-icon { --mdc-icon-size: 20px; }
  .count { font-size: 13px; color: var(--dl-text-2); font-weight: 400; }
`;

class DelonghiDrinksCardEditor extends HTMLElement {
  constructor() {
    super();
    this._root = this.attachShadow({ mode: "open" });
    this._machines = null;
  }

  setConfig(config) {
    const next = { ...(config || {}) };
    // Our own change coming back: keep the page (and focus) as it is.
    if (this._config && JSON.stringify(next) === JSON.stringify(this._config)) return;
    this._config = next;
    this._render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) {
      loadMachines(hass)
        .then((machines) => (this._machines = machines))
        .catch(() => (this._machines = []))
        .finally(() => this._render());
    }
  }

  _change(patch) {
    const next = { ...this._config, ...patch };
    for (const [key, value] of Object.entries(next)) if (value === undefined || value === "") delete next[key];
    this._config = next;
    this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: next }, bubbles: true, composed: true }));
  }

  _machine() {
    if (!this._machines) return null;
    return this._machines.find((m) => m.entry_id === this._config.entry_id) || this._machines[0] || null;
  }

  _render() {
    if (!this._config) return;
    this._root.innerHTML = `<style>${SHARED_STYLE}${EDITOR_STYLE}</style>`;
    const machine = this._machine();

    const title = element("input", {
      type: "text", id: "title", value: this._config.title || "",
      placeholder: (machine && machine.name) || "Coffee",
      onchange: () => this._change({ title: title.value.trim() }),
    });
    this._root.append(element("div", { class: "field" }, element("label", { for: "title", text: "Title" }), title));

    if (this._machines && this._machines.length > 1) {
      const pick = element("select", {
        class: "choice", id: "machine",
        onchange: () => this._change({ entry_id: pick.value, drinks: undefined }) || this._render(),
      });
      for (const m of this._machines) pick.append(element("option", { value: m.entry_id, text: m.name }));
      pick.value = machine.entry_id;
      this._root.append(element("div", { class: "field" }, element("label", { for: "machine", text: "Machine" }), pick));
    }

    const size = this._config.size || "small";
    const segments = element("div", { class: "segments", role: "group", "aria-label": "Tile size" });
    for (const [key, label] of Object.entries(SIZES)) {
      segments.append(element("button", {
        text: label, "aria-pressed": String(key === size),
        onclick: () => {
          this._change({ size: key === "small" ? undefined : key });
          this._render();
        },
      }));
    }
    this._root.append(element("div", { class: "field" }, element("span", { class: "heading", text: "Tile size" }), segments));

    if (!machine) {
      if (this._machines) this._root.append(element("p", { class: "hint", text: "No De’Longhi machine is set up in Home Assistant." }));
      return;
    }
    this._root.append(this._drinksField(machine));
  }

  _drinksField(machine) {
    const available = machine.drinks.filter((d) => this._hass.states[d.entity_id]);
    const choosing = Array.isArray(this._config.drinks);
    const chosen = choosing ? this._config.drinks.map(Number).filter((id) => available.some((d) => d.id === id)) : [];
    const field = element("div", { class: "field" });

    const all = element("button", {
      class: "toggle", role: "switch", id: "all-drinks", "aria-checked": String(!choosing),
      onclick: () => {
        // Start choosing from everything shown today, so nothing disappears by surprise.
        this._change({ drinks: choosing ? undefined : available.map((d) => d.id) });
        this._render();
      },
    });
    field.append(element("div", { class: "switch-row" }, element("label", { for: "all-drinks", text: "Show all drinks" }), all));
    if (!choosing) {
      field.append(element("p", { class: "hint", text: "Turn off to choose which drinks this card shows, and in what order." }));
      return field;
    }

    field.append(element("p", { class: "hint" },
      `${chosen.length} of ${available.length} drinks. Drinks switched off on the device page, like the Mug versions, aren’t listed.`));
    const list = element("div", { class: "list", role: "list" });
    const ordered = [...chosen.map((id) => available.find((d) => d.id === id)), ...available.filter((d) => !chosen.includes(d.id))];
    ordered.forEach((drink) => {
      const on = chosen.includes(drink.id);
      const index = chosen.indexOf(drink.id);
      const box = element("input", {
        type: "checkbox", checked: on, "aria-label": drink.name,
        onchange: () => {
          this._change({ drinks: on ? chosen.filter((id) => id !== drink.id) : [...chosen, drink.id] });
          this._render();
        },
      });
      const move = (delta) => {
        const next = [...chosen];
        next.splice(index, 1);
        next.splice(index + delta, 0, drink.id);
        this._change({ drinks: next });
        this._render();
        this._root.querySelector(`[data-move="${drink.id}:${delta}"]`)?.focus();
      };
      const up = element("button", { class: "move", "aria-label": `Move ${drink.name} up`, disabled: !on || index === 0, onclick: () => move(-1) });
      up.innerHTML = '<ha-icon icon="mdi:chevron-up"></ha-icon>';
      up.dataset.move = `${drink.id}:-1`;
      const down = element("button", { class: "move", "aria-label": `Move ${drink.name} down`, disabled: !on || index === chosen.length - 1, onclick: () => move(1) });
      down.innerHTML = '<ha-icon icon="mdi:chevron-down"></ha-icon>';
      down.dataset.move = `${drink.id}:1`;
      const thumb = drink.photo ? element("img", { class: "thumb", src: drink.photo, alt: "", loading: "lazy" }) : element("span", { class: "thumb" });
      const item = element("div", { class: "item", role: "listitem", draggable: on }, box, thumb,
        element("span", { class: "label", text: drink.name }), up, down);
      if (on) this._dragAndDrop(item, drink.id, chosen);
      list.append(item);
    });
    field.append(list);
    return field;
  }

  /** Reorder chosen drinks by dragging, with a mouse; the arrow buttons work everywhere. */
  _dragAndDrop(item, id, chosen) {
    item.addEventListener("dragstart", (e) => {
      e.dataTransfer.setData("text/plain", String(id));
      e.dataTransfer.effectAllowed = "move";
      item.classList.add("dragging");
    });
    item.addEventListener("dragend", () => item.classList.remove("dragging"));
    item.addEventListener("dragover", (e) => e.preventDefault());
    item.addEventListener("drop", (e) => {
      e.preventDefault();
      const moving = Number(e.dataTransfer.getData("text/plain"));
      if (!chosen.includes(moving) || moving === id) return;
      const next = chosen.filter((x) => x !== moving);
      next.splice(next.indexOf(id), 0, moving);
      this._change({ drinks: next });
      this._render();
    });
  }
}

if (!customElements.get(TAG)) {
  customElements.define(TAG, DelonghiDrinksCard);
  customElements.define(EDITOR_TAG, DelonghiDrinksCardEditor);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: TAG,
    name: "Coffee drinks",
    description: "Make a drink on your De’Longhi coffee machine and change its settings.",
    documentationURL: "https://github.com/bek00letz/delonghi-daedalus-ha#drinks-card",
  });
}
