/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Absolute API origin. Leave unset to stay same-origin on `/api` (recommended). */
  readonly VITE_API_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
