# Web client boundary

Owns the responsive JoeOS workspace, widget composition, configuration previews, keyboard/touch interactions, data freshness states, and accessible command surfaces.

Current implementation and Sites identity live in the nested `../../joeos-web` Git repository. Its commit history and `.openai/hosting.json` must be preserved during a later subtree migration. Until React component parity is reached, the root single-file dashboard is the operational compatibility shell and the iframe dashboard is a reference/demo surface.

Web code consumes only `packages/sdk` and versioned contracts. It never stores restricted credentials or directly invokes local executables.
