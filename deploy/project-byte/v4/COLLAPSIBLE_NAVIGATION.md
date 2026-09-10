# Collapsible navigation

Navigation starts hidden on every load. A quiet, 44-pixel touch target labeled Navigation opens the existing five destinations; tapping again or pressing Escape within the dock closes it. Choosing a destination closes the dock and returns the workspace scroll area to its top. The control exposes its expanded state and controlled navigation region to assistive technology; collapsed destinations are hidden and inert.

The page is a vertical layout containing the existing header, a flexible scrollable workspace, and the navigation footer. The footer participates in layout: opening it reduces the workspace viewport by its actual height instead of covering content. Bottom content remains reachable, including while docked. The visible viewport height and safe-area inset are respected on resize; browser keyboard/viewport changes can shrink the content region. No overlay or backdrop is used for navigation.

The original appearance and five destinations are preserved. Drag edge-scrolling now targets the workspace scroll region; task Undo and Home feedback sit above the measured navigation footer. Data, authentication, settings, execution routes, and worker state are unchanged. No service restart is required.

Browser verification covers 320, 390, 844 landscape, and 1440 widths, actual content-height changes, non-overlapping bounds, scroll-to-end reachability, touch toggle and routing, Enter/Tab/Escape, draft preservation, and collapsed state after reload. Existing browser fixtures open the dock before using its destinations. Physical phone keyboard behavior is not claimed as hardware-tested.
