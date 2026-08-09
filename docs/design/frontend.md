# Frontend

Single-page app with hash-based routing. Vanilla JS modules under
`frontend/static/js/`. No build step.

## Layout

```
frontend/
├── templates/index.html       Single HTML shell; loads /static/js/main.js as ES module
└── static/
    ├── css/
    │   ├── tokens.css         design tokens (light + dark + reduced-motion)
    │   └── app.css            reset + components + layout
    ├── js/
    │   ├── main.js            boot, theme cycle, router
    │   ├── api.js             thin fetch wrapper
    │   ├── cache.js           LLM lookup cache in localStorage
    │   ├── state.js           tiny pub/sub store
    │   ├── components/
    │   │   ├── nav-links.js
    │   │   ├── lang-switcher.js
    │   │   └── toast.js
    │   └── pages/
    │       ├── dictionary.js
    │       ├── review.js
    │       ├── structures.js
    │       ├── phrases.js
    │       └── settings.js
    ├── manifest.json
    └── sw.js                  pass-through
```

## Routing

```
#/dictionary   search + result card + "Look up with AI" button
#/review       Leitner session (recall + grade)
#/structures   built-in + user-added structures
#/phrases      built-in + user-added phrases
#/settings     general + dict chain + per-language initialize
```

`main.js` handles `hashchange` events. Each page module is loaded lazily
with dynamic `import()` so the first paint stays small.

## State

`state.js` exports a single store with a tiny pub/sub. Initial state:

```js
{
  settings: null,         // user settings object
  languages: [],          // catalog with seeded flags
  activeLanguage: null,   // mirrors settings.active_language
  initialized: false,
}
```

Subscribers are notified after every `store.set()`. The lang switcher
re-renders itself; pages re-fetch when language changes.

## API wrapper

`api.get/post/put/del` returns `{ok, data, error, status}`. Network errors
become `{ok:false, error:"network_error"}`. Non-JSON responses become
`{ok:false, error:"invalid_json"}`. HTTP 401 placeholder is reserved for
future auth (no UI today).

## LLM dictionary cache

`cache.js` keeps `lang:word → entry` in `localStorage` under namespace
`langlearn:dict:v1`. LRU-capped at 1000 entries per language. On quota
errors it evicts the oldest half and tries again.

## Design tokens

See `docs/design/ui-tokens.md` and the css file itself. Light theme is
the default; `[data-theme="dark"]` overrides; `prefers-reduced-motion`
disables transitions globally.

## Conventions

- All untrusted strings go through `escapeHtml` before insertion. The
  function lives in each page module (small, no shared util file).
- Avoid `innerHTML` — prefer building DOM nodes with
  `document.createElement`.
- All async work surfaces failures through the toast component, never
  via silent logging.

## Keyboard shortcuts

| Key | Page | Action |
|---|---|---|
| `/` | global | focus dictionary search |
| `Space` | review | reveal answer |
| `1` | review | grade hard |
| `2` / `Enter` | review | grade easy |

Handlers attached in `pages/dictionary.js` and `pages/review.js`.