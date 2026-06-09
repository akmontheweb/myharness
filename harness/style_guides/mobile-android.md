---
applies_to: [android]
---

## Android Style Guide (Material Design 3)

### Source
- Material Design 3 (https://m3.material.io)
- Material Symbols (https://fonts.google.com/icons)
- Android Compose Material 3 (https://developer.android.com/jetpack/androidx/releases/compose-material3)

### Philosophy
Material 3 (a.k.a. Material You) is expressive, personal, and adaptive. UIs reshape around the user's wallpaper-derived dynamic color, their text-size preferences, and the device's window size class. Build with M3 components, M3 tokens, and the M3 type scale by default — falling back to Material 2 conventions is a regression.

### Color — roles, not paint chips
- Build with M3 **color roles**, never raw hex codes scattered through the code. The role names are the contract:
  - `primary`, `onPrimary`, `primaryContainer`, `onPrimaryContainer`
  - `secondary`, `onSecondary`, `secondaryContainer`, `onSecondaryContainer`
  - `tertiary`, `onTertiary`, `tertiaryContainer`, `onTertiaryContainer`
  - `error`, `onError`, `errorContainer`, `onErrorContainer`
  - `surface`, `onSurface`, `surfaceVariant`, `onSurfaceVariant`
  - `surfaceContainerLowest`, `surfaceContainerLow`, `surfaceContainer`, `surfaceContainerHigh`, `surfaceContainerHighest`
  - `outline`, `outlineVariant`, `inverseSurface`, `inverseOnSurface`, `inversePrimary`, `scrim`
- The `on*` color is the foreground guaranteed to clear contrast against its paired surface. Always pair them — `primary` text on `onPrimary` is a bug.
- **Dynamic color** (Material You) on Android 12+ (API 31): seed the color scheme from `dynamicLightColorScheme(context)` / `dynamicDarkColorScheme(context)`. Provide a static brand color scheme as the fallback for older APIs and for branded contexts where personalization shouldn't override identity.

### Typography
- M3 type scale: `displayLarge / displayMedium / displaySmall`, `headlineLarge / headlineMedium / headlineSmall`, `titleLarge / titleMedium / titleSmall`, `bodyLarge / bodyMedium / bodySmall`, `labelLarge / labelMedium / labelSmall`. Don't roll custom sizes — pick the closest role.
- Default brand font: Roboto (system) or Google Sans where licensed; assign through `Typography` in `MaterialTheme`.
- Respect `fontScale` from system settings — never hard-pin `sp` values to `dp`. The M3 type scale already uses `sp` correctly.

### Shape
- Six corner-radius tokens: `none (0dp)`, `extraSmall (4dp)`, `small (8dp)`, `medium (12dp)`, `large (16dp)`, `extraLarge (28dp)`. Apply via the `Shapes` slot of `MaterialTheme`.
- Components have prescribed defaults: chips → `small`, cards → `medium`, dialogs → `extraLarge`, FAB → `large`. Don't override unless the brand explicitly requires it.

### Elevation — tonal first, then shadow
- M3 communicates elevation primarily through **tonal color shifts** (the surface tints higher containers with primary at increasing alpha). Use `Surface(tonalElevation = 3.dp)` rather than slapping a shadow on a card.
- Reserve drop shadows for genuinely floating elements (FAB, snackbar, dialog scrim). Layered cards should rely on `surfaceContainer*` levels for separation.

### Components — use M3 versions
- App chrome: `TopAppBar` (center-aligned for short titles, large for hero pages), `NavigationBar` (bottom, 3–5 destinations), `NavigationRail` (compact-medium width tablets), `NavigationDrawer` (expanded width).
- Actions: `Button` / `FilledButton` (high emphasis), `FilledTonalButton` (medium), `OutlinedButton` (medium), `TextButton` (low). `FloatingActionButton` for the single primary action on a screen.
- Inputs: `OutlinedTextField` (default) and `TextField` (filled). Both expose `supportingText`, `leadingIcon`, `trailingIcon`, and error states.
- Selection: `Checkbox`, `RadioButton`, `Switch`, `Chip` (assist/filter/input/suggestion variants), `SegmentedButton`.
- Surface: `Card`, `ListItem`, `Divider`, `Snackbar`, `AlertDialog`, `BottomSheet`, `ModalBottomSheet`.

### Motion
- Use M3 motion tokens, not hand-tuned curves:
  - **Standard easing** `cubic-bezier(0.2, 0.0, 0, 1.0)` for most transitions
  - **Emphasized easing** `cubic-bezier(0.2, 0.0, 0, 1.0)` (decelerate) for entering elements, `(0.3, 0.0, 0.8, 0.15)` (accelerate) for exits
- Durations: short1–short4 (50–200 ms), medium1–medium4 (250–400 ms), long1–long4 (450–600 ms). Use short for state changes, medium for component transitions, long for full-screen navigation.
- Honor `Settings.Global.ANIMATOR_DURATION_SCALE` and the system Reduce Motion preference — disable non-essential motion when set.

### Layout & adaptive design
- Design for **window size classes**: Compact (< 600 dp), Medium (600–839 dp), Expanded (≥ 840 dp). Use `WindowSizeClass` to switch between NavigationBar / NavigationRail / NavigationDrawer.
- Edge-to-edge content: opt in with `WindowCompat.setDecorFitsSystemWindows(window, false)`, then use `WindowInsets` / `safeDrawingPadding` to keep content out of the status bar and gesture inset.
- Minimum touch target: **48 × 48 dp**. Compose enforces this by default for Material 3 components — don't disable it.

### Accessibility
- TalkBack labels on every interactive: `contentDescription` (non-null for icons, `null` for decorative). Group related items into one announce unit when they form a logical control.
- Text contrast 4.5:1 minimum (3:1 for ≥ 18 sp / bold). M3 `on*` roles handle this for you when paired correctly.
- Support large fonts, bold text, and high-contrast modes. Test at `fontScale = 2.0` and verify nothing clips.
- Provide haptic confirmation on actions: `view.performHapticFeedback(HapticFeedbackConstants.CONFIRM)`.

### Flutter on Android specifically
- Always opt into Material 3: `MaterialApp(theme: ThemeData(useMaterial3: true, colorScheme: ...))`.
- Generate the color scheme from a brand seed with `ColorScheme.fromSeed(seedColor: ...)` — it produces all the M3 roles. Use `dynamic_color` package to read the system seed on Android 12+.
- Prefer the new Material 3 widgets: `NavigationBar` (not `BottomNavigationBar`), `NavigationRail`, `FilledButton`, `FilledTonalButton`, `SegmentedButton`, `SearchBar`, `Badge`. Older Material 2 widgets still work but mix styles.
- Use `MediaQuery.of(context).platformBrightness` plus `ThemeData.dark()` so the app follows the system theme.
- For edge-to-edge on Android, `SystemChrome.setEnabledSystemUIMode(SystemUiMode.edgeToEdge)` and wrap content in `SafeArea` or apply `MediaQuery.viewPadding`.
