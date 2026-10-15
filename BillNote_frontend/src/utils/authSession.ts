export const AUTH_SESSION_KEY = 'bilinote-session-token'

// This is an application login token, never a model key or downloader Cookie.
// Only the login token is saved; passwords remain in the browser password manager.
export const getAuthToken = () => sessionStorage.getItem(AUTH_SESSION_KEY) || localStorage.getItem(AUTH_SESSION_KEY)

export const setAuthToken = (token: string | null, remember = false) => {
  if (token && remember) {
    localStorage.setItem(AUTH_SESSION_KEY, token)
    sessionStorage.removeItem(AUTH_SESSION_KEY)
  } else {
    if (token) sessionStorage.setItem(AUTH_SESSION_KEY, token)
    else sessionStorage.removeItem(AUTH_SESSION_KEY)
    localStorage.removeItem(AUTH_SESSION_KEY)
  }
}
