const SESSION_KEY = 'bilinote-session-token'

// This is an application login token, never a model key or downloader Cookie.
// sessionStorage survives refreshes but is scoped to the current browser tab.
export const getAuthToken = () => sessionStorage.getItem(SESSION_KEY)

export const setAuthToken = (token: string | null) => {
  if (token) sessionStorage.setItem(SESSION_KEY, token)
  else sessionStorage.removeItem(SESSION_KEY)
}
