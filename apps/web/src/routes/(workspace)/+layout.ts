// The workspace is a client-rendered app shell talking to FastAPI with the
// user's token. SSR adds nothing here and would duplicate the session dance.
export const ssr = false;
