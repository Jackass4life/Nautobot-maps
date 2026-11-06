// The app's colours in Nuxt UI (#463): its blue, and the alarm colours of the
// old pages (static/css/alerts.css).  Read by app.config.ts and, at build
// time, by modules/static-ui-colors.ts.
export const UI_COLORS = {
  primary: "blue",
  info: "blue",
  success: "green",
  warning: "amber",
  error: "red",
  neutral: "slate",
} as const;
