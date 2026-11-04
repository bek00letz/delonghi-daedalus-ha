# AGENTS.md

## Project: De'Longhi Daedalus Home Assistant Integration (`delonghi_daedalus`)

This repository provides a modern, high-performance Home Assistant custom integration for De'Longhi coffee machines utilizing the **Daedalus stack** (including the **Eletta Ultra ECAM 470.85** and My Coffee Lounge ecosystem). It supports both direct local network control (`/ws/lan2lan` WebSocket) and cloud shadow fallback (SAP Gigya + AWS IoT Core).

---

# Apple Craftsmanship & Polish Standard

Any piece of software, interface, component, script, or artifact created in this workspace must look, feel, and function like something Apple shipped.

This is not about superficial aesthetics (it is not just "white backgrounds" or "liquid glass"), but about the uncompromising, obsessive attention to the subtle details that elevate a product from merely functional to undeniably polished, intuitive, and tactile.

## 1. The Core Philosophy
- **Restraint and Intentionality**: Every element, border, gradient, and margin must have a clear reason to exist. If an element doesn't serve the user or clarity, eliminate it. Chrome should recede so content takes center stage.
- **The "Invisible" Polish**: The best design details are the ones the user feels rather than notices—fluid transitions, forgiving touch targets, instantaneous responses, and seamless state preservation.
- **Respect for the Human**: Never confuse, startle, or overwhelm the user. Protect user agency, avoid dark patterns, avoid gratuitous modal interruptions, and respect system preferences (`prefers-color-scheme`, `prefers-reduced-motion`, `prefers-contrast`).

## 2. Visual & Typographic Hierarchy
- **Typography as Architecture**:
  - Use clean, native system font stacks (`-apple-system`, `BlinkMacSystemFont`, `SF Pro Display`, `SF Pro Text`, `system-ui`).
  - Strict typographic scale with intentional line heights (`leading`), letter spacing (`tracking`), and contrast hierarchies (primary, secondary, tertiary labels).
  - Proper typographic punctuation: real quotes (`“ ”`), apostrophes (`’`), em dashes (`—`), and ellipsis (`…`).
- **Concentric Geometry**:
  - Nested corners must be mathematically concentric: $R_{\text{inner}} = R_{\text{outer}} - \text{padding}$. Never have sharp-cornered children inside rounded containers.
  - Standard Apple squircle/continuous corner curves (`corner-smoothing: 60%` / smooth continuous curvature) where supported.
- **Subtle Depth & Elevation**:
  - Soft, multi-layered ambient drop shadows rather than harsh single-offset dark shadows.
  - Delicate hairline borders (e.g., 0.5px or 1px subtle rgba overlays matching dynamic contrast) to define surfaces against any background.
  - Intentional translucent materials only when functional (floating toolbars, overlays, headers), never stacked gratuitously.

## 3. Motion, Physics & Tactility
- **Natural, Organic Spring Physics**:
  - No linear or robotic easing curves. Use mass, stiffness, and damping curves (e.g., Apple-style fluid spring animations: `cubic-bezier(0.25, 1, 0.5, 1)` or fluid springs).
  - Transitions must feel physically grounded—elements expand from their origin, decelerate naturally, and settle with weight.
- **Micro-Interactions & State Polish**:
  - Every interactive surface must respond instantly to touch, hover, and press (subtle scale, luminance shift, or highlight).
  - Zero jarring layout shifts (CLS): reserve space for dynamic content, images, and asynchronous data.
  - Meaningful loading states: use subtle skeleton loaders or gentle shimmering placeholders rather than jarring generic spinners.

## 4. Interaction & Functional Elegance
- **Forgiving & Resilient**:
  - Generous hit targets (minimum 44x44pt on touch, comfortable click zones on pointer).
  - Inline validation that is helpful and constructive, not punitive.
  - Preserve user inputs and state across navigation or transient network blips.
- **Sensible, Thoughtful Defaults**:
  - Pre-configure the most likely, safest, and highest-quality default choice.
  - Confirmation required only for truly destructive, irreversible actions—make reversible actions easy to undo instead.
- **Zero "Sludge" & Cognitive Clutter**:
  - Eliminate redundant badges, unnecessary tooltips, cluttering icons, and marketing popovers.
  - Clear, concise, active human copy. Avoid jargon, robotic messages, or ambiguous button labels (e.g. use "Delete Note" instead of "OK").

## 5. Artifacts, Code & Output Standards
- **Artifacts**: Clean, scannable, well-typeset Markdown with elegant tables, clear visual callouts, structured sections, and zero boilerplate filler.
- **Code & CLI Tools**: Clean terminal formatting, clear progress indicators, graceful failure exits, helpful error messages with actionable remedy hints, and polished CLI help menus.

