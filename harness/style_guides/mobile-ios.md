---
applies_to: [ios]
---

## iOS Style Guide (Apple Human Interface Guidelines)

### Source
- Apple Human Interface Guidelines (https://developer.apple.com/design/human-interface-guidelines)
- SF Symbols (https://developer.apple.com/sf-symbols/)

### Philosophy
Build for clarity, deference, and depth. The UI should defer to content: chrome stays minimal, motion supports task flow, depth and translucency communicate hierarchy. iOS users expect every Apple convention — surprise them only when the surprise is materially better than the standard.

### Layout & safe areas
- Honor the safe area on every screen. Never place tappable controls under the status bar, the home indicator, or the Dynamic Island. In SwiftUI use `.safeAreaInset` / `.ignoresSafeArea` deliberately; in UIKit anchor to `view.safeAreaLayoutGuide`. In Flutter wrap mobile-first screens in `SafeArea`.
- Standard horizontal margins: 20 pt on iPhone, 20 pt on iPad regular width; content insets adapt to size class.
- Minimum tap target: **44 × 44 pt**. Smaller hit targets are an Apple-flagged review issue. Pad icon-only controls until they meet the minimum even when the glyph is smaller.
- Support every dynamic size: portrait, landscape, Split View, Stage Manager, external display, large content viewer.

### Typography
- Use the system font (San Francisco — `SF Pro Text` for ≤ 20 pt, `SF Pro Display` for ≥ 20 pt). Don't ship custom fonts unless the brand requires it; system fonts get Dynamic Type, optical sizing, and tracking for free.
- Adopt the semantic text styles (Largetitle, Title 1/2/3, Headline, Body, Callout, Subheadline, Footnote, Caption 1/2). They re-scale automatically with Dynamic Type and respect Bold Text accessibility setting.
- In Flutter, prefer `CupertinoTextThemeData` / `CupertinoTheme.of(context).textTheme` over hand-rolled `TextStyle` values on iOS.

### Color
- Use system semantic colors (`UIColor.label`, `.secondaryLabel`, `.systemBackground`, `.secondarySystemBackground`, `.tertiarySystemBackground`, `.systemFill`, `.separator`). They auto-adapt to Light / Dark / High Contrast / increased contrast modes.
- Choose ONE accent color (the "tint") per app — it propagates to controls, links, and selection. Set it once in `Info.plist` (`UIAppearance.tintColor`) / SwiftUI `.tint(_:)`.
- Don't communicate state with color alone — pair it with a symbol or label so it survives color-blind users and grayscale modes.

### SF Symbols
- Use SF Symbols (5,000+ system glyphs) for any standard icon. They align with text baselines, respect weight, and ship in every weight and scale automatically.
- Custom icons must match SF Symbol stroke weight and visual mass — otherwise they look pasted on.

### Navigation
- One primary navigation style per app. Pick one:
  - **Tab bar** (3–5 destinations, persistent) for peers at the top of the IA.
  - **Navigation stack** (drill-down) for hierarchical data.
  - **Sidebar** (iPad regular width) when the IA is deep and the device is large.
- Title bars: large title on the root of a stack, inline title once the user drills in or starts scrolling.
- Back button always carries the previous screen's title — don't override unless required for clarity.

### Controls & feedback
- Use standard control sizes (`small`, `medium`, `large` in SwiftUI). Match heights across a row.
- Pull-to-refresh and swipe actions are expected on lists; provide them when the data model supports refresh or row mutations.
- Standard gestures only: pinch zoom, edge-swipe back, long-press for context. Don't reassign them.
- Haptics: `UIImpactFeedbackGenerator(.light/.medium/.rigid)` for tactile confirmation, `UINotificationFeedbackGenerator` for success / warning / error. Use sparingly — every haptic costs the user attention.
- Provide both light and dark appearance for every custom asset (`Assets.xcassets` with appearance variants).

### Accessibility
- VoiceOver is the contract: every interactive element gets an `accessibilityLabel`; decorative content gets `accessibilityHidden = true`. Group related elements with `accessibilityElement(children: .combine)`.
- Test with Dynamic Type at the largest accessibility size (5×). If layout breaks, fix it — don't pin font sizes.
- Color contrast: 4.5:1 minimum for body, 3:1 for ≥ 18 pt or bold. The system colors clear this automatically; custom palettes must be checked.
- Respect Reduce Motion (`UIAccessibility.isReduceMotionEnabled`), Reduce Transparency, Smart Invert, and Differentiate Without Color.

### Flutter on iOS specifically
- Build platform-adaptive UI: use `CupertinoApp` for an iOS-only target, or check `Theme.of(context).platform == TargetPlatform.iOS` (or `defaultTargetPlatform`) and branch to `Cupertino*` widgets in mixed-platform apps.
- Prefer `CupertinoNavigationBar`, `CupertinoTabScaffold`, `CupertinoButton`, `CupertinoTextField`, `CupertinoActionSheet`, `CupertinoSlider`, `CupertinoSwitch`, `CupertinoPicker`, and `CupertinoDatePicker` over the Material equivalents on iOS.
- Use `CupertinoPageRoute` for navigation transitions so the back-swipe and slide animations match the platform.
- iOS app icons: ship at every required size in the asset catalog; the icon should read clearly at 60 × 60 pt without losing detail.
- Status bar style: set per screen so it harmonizes with the navigation bar.
