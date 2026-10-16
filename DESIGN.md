# Aptus design: brand layer

Purpose: record how Aptus applies the Stump & Root app family design system (version 2.1, October 2026). The family doc is the authority. This doc adds only Aptus's brand layer and never contradicts the family doc without a note here. Agents: read the family doc first, then this doc, and ask before inventing a color, material, radius, or component.

## Profile

| Field | Aptus |
|---|---|
| Platforms | Mac (pywebview build; native later), plus the browser preview |
| Sensibility | Pro creative tool: instant response, minimal motion, the photo leads |
| Signature accent | Sage (approved by Tim, October 16, 2026). Dino Day also proposes sage; the apps don't share a screen, so the overlap is acceptable |
| Supporting | Butter and persimmon (Apti's brackets, crop outlines, warnings) |
| Display face | None. SF Pro for all UI; SF Mono for data |
| Glass use | Inset sidebar, phone tab bar, right-click menu, and the 100% check bar over the photo. The photo well, cards, and inspector stay opaque |
| Tactile emphasis | Raised Export key, inset photo well and scrubber, card motion on export results (4°) and batch tray tiles (2°) |
| Celebrate moment | Apti's hop, on the first export of each session only |

## Tokens in use

The web build keeps every token in the `:root` block at the top of `static/index.html`. Values match the family doc.

| Group | Aptus values |
|---|---|
| Neutrals | `paper`, `reader`, `card`, `bar`, `ink`, `textSecondary`, `textTertiary`, `hairline`, `outlineStrong`, `interactive`, `onInteractive` (light and dark) |
| Accent | `sage` `#AFC398` / `#93A87C`; text-safe `#4F6440` / `#B9CDA3`; wash at 34% light, 24% dark |
| Supporting | `butter` `#F4C76B` (text-safe `#8A6414`), `persimmon` `#FA6B47` (text-safe `#B23F24`) |
| Radius | 9 buttons and fields, 13 rows and result cards, 17 panels, 18 the sidebar |
| Motion | `instant`, `calm` 300 ms, `card` 300 ms `cubic-bezier(.2,.8,.2,1)`, `press` 100 ms, `snappy` 200 ms, `touch` 150 ms, one `celebrate` |

## How each part maps to the family components

| Aptus part | Family component | Notes |
|---|---|---|
| Sidebar | Sidebar (inset glass) | 8 pt inset, 18 pt radius, paper-tinted glass, light rim on the top-left; selected row is a reader-white sheet with a sage marker |
| Export | Primary button, raised | Ink key with a top highlight; one per view; right-click opens its options |
| Add files, Start over, chips | Secondary button | Card fill, hairline that firms on hover |
| Destination row | Sidebar row pattern | Hover tint; selected row is a reader sheet with a sage marker and a sage check box (ink check, 6.8:1) |
| Photo well, scrubber, text fields, file-name preview | Inset field or well | Reader or bar fill, 1 pt top inner shadow |
| Result card | Feature card | Card motion at 4° tilt, 2 pt lift, edge light, sheen, press at 0.98 |
| Batch tray tile | Document row | Card motion at 2°; current file gets an ink focus outline; done files get a labeled sage check |
| Section headers | Section header | Diamond, mono overline, dotted rule |
| Toast | Toast (fixed ink) | 4.5 s, announced to VoiceOver |
| Empty state | Empty state | Apti on a sage-tinted disc, one sentence, one action (plus a quiet "Try the sample photo" link) |
| Warnings | Status with icon | Butter wash, ink text, "!" icon. Errors use a system red "!" icon with ink text |

## Right-click options on Export

The menu lists, in order:

1. The main action ("Export N files").
2. **Save to:** Desktop › Aptus Exports, the chosen folder (checked when current), and Choose folder… on a Mac.
3. **File names:** Original file name, SEO names…, and Number the files, each with a checkmark when on.
4. **Export settings…**, which opens Settings.

Every option is also reachable in Settings or the File names panel. Shift-F10 or the Menu key opens it from the keyboard.

## Apti

Apti keeps its shape and animations, recolored to the palette:

- Body: sage (`#AFC398`).
- Face: ink (`#26352E`).
- Brackets: persimmon at the top-left and bottom-right, butter at the top-right and bottom-left.

Apti blinks while idle. While files export, the brackets close in on the frame and back, so progress shows where you're already looking. After the first export of a session, Apti hops once. Under Reduce Motion, Apti holds still.

## Motion and interaction

Aptus uses the family tokens only (`calm` and `card` 300 ms, `snappy` 200 ms, `touch` 150 ms, `press` 100 ms), animates only transform and opacity, and never bounces a control. Keyboard actions stay instant. Under Reduce Motion, every movement below becomes a crossfade and Apti holds still.

| Moment | What moves | Token |
|---|---|---|
| Changing pages | The new page slides in the direction you travel: down the sidebar it rises from below, back up it drops from above, and the old page lifts away. ⌘, and ⌘E switch instantly | `calm` in, 180 ms out |
| Sidebar and phone tabs | One reader-white selection sheet slides between rows instead of jumping | `calm` |
| Opening a file | The work area rises in once | `calm` |
| Adding files to a batch | New tray tiles scale in | `snappy` |
| Removing a file | The tile shrinks out, then the others slide over to close the gap | 150 ms, then `calm` |
| Choosing a destination | Its check draws in; its crop frame appears | `snappy` |
| Hovering a destination | Its crop frame turns solid and thicker on the photo, so you can see which outline is which | Instant |
| Tapping the subject or resetting a crop | Every crop frame glides to its new place. Dragging a frame and arrow-key nudges stay instant | 300 ms ease-out |
| Export button | The label morphs: "Export 4 files" → "Fitting 1 of 2…" → "✓ Saved 4 files", holds 1.8 s, then morphs back | 100 ms out, 180 ms in |
| Results | Cards rise in one after another, 40 ms apart (up to 8) | `calm` |
| Result cards and tray tiles | Card motion (tilt, lift, edge light, sheen, press) | `card` |
| Check at 100% | The image flies out of its thumbnail and back into it on close. The Export and Original selection slides. Esc closes instantly | `calm` |
| Right-click menu, 100% view, drop overlay | Fade out instead of vanishing. Esc and Tab close menus instantly | 120–200 ms |

### Apti's reactions

Apti reacts and never asks for attention.

| Event | Apti |
|---|---|
| Idle | Blinks. Its eyes follow the pointer, up to 2.8 units inside a 120-unit face, but hold still while the pointer is over the photo or you're dragging a crop |
| Files dragged over the window | Perks up: the body lifts and the brackets open wide |
| Files dropped | The brackets snap shut on the frame, then the overlay fades |
| Exporting | The brackets close in and back on the Export button |
| First export of a session | The one celebration: Apti hops and raises its brackets |
| Later exports | A small nod: the brackets tap in once |
| An export fails | Worried: a flat mouth and drooping brackets, in a butter "Stopped" block with the reason |

## Crop outlines

Crop frames on the photo use family colors in this order: persimmon, sage text-safe, butter, petal text-safe, ink, sage, persimmon text-safe, butter text-safe. Each frame also carries a text label with its destination and size, so color is never the only cue.

## App icon

| Layer | File |
|---|---|
| Background | `mac/icon/background-sage.svg` (flat sage; `background-ink.svg` and `background-paper.svg` for the alternates) |
| Foreground | `mac/icon/foreground-ink.svg`: four crop brackets around a focal dot, about 60% of the canvas (`foreground-paper.svg` for the ink alternate) |
| Interim build icon | `mac/Aptus.icns`, a flat render of the sage version for the pywebview build |

The glyph follows the family rules but started as a construction, not a paper sketch. Before release, sketch the metaphor by hand, then build the final layers in Icon Composer 2. Apply glass to the foreground group only, and check Default, Dark, Clear, and Tinted at 16, 32, and 64 pt on real devices.

## What changed from the first Aptus look

- Teal (`#3FB8A4`), coral, lime, and iris are gone. Sage is the accent, with butter and persimmon supporting.
- Avenir Next, Nunito, and the Caveat handwriting are gone. SF Pro and SF Mono replace them, with mono overlines where the handwriting was.
- Pill buttons with a bouncy release became 9 pt keys with press depth and no bounce.
- Filled icons became stroked family glyphs.
- The staggered rise-in and the bouncing wordmark became a single 300 ms fade.
- The celebration plays once per session instead of on every export.
