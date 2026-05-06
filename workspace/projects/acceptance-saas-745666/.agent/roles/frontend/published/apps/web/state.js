export const appState = {
  filters: { status: 'all', owner: 'all' },
  projects: [
    { id: 'p-100', name: 'Atlas Migration', health: 'On Track', openTasks: 12 },
    { id: 'p-200', name: 'Tenant Console', health: 'At Risk', openTasks: 7 },
    { id: 'p-300', name: 'Release Readiness', health: 'Review', openTasks: 4 }
  ]
};

export function setFilter(name, value) {
  appState.filters[name] = value;
  return { ...appState.filters };
}

export function summarizeProjects(projects = appState.projects) {
  return projects.reduce((summary, project) => {
    summary.total += 1;
    summary.openTasks += project.openTasks;
    summary.byHealth[project.health] = (summary.byHealth[project.health] || 0) + 1;
    return summary;
  }, { total: 0, openTasks: 0, byHealth: {} });
}
