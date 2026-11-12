// What the current page shows in the shared AppShell: its title row (title, description, actions) and its widget
// column. Each page sets these through <Page>; the root layout hands them to AppShell.
export const shell = $state({ title: '', description: '', actions: null, widgets: null, crumb: null });
