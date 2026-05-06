import { fetchHealth } from './api.js';
import { appState, summarizeProjects } from './state.js';

const status = document.getElementById('status');
const button = document.getElementById('ping');
const summary = summarizeProjects(appState.projects);

document.querySelector('.card ul')?.insertAdjacentHTML(
  'beforeend',
  `<li>${summary.total} active project slices</li><li>${summary.openTasks} open implementation tasks</li>`
);

button?.addEventListener('click', async () => {
  status.textContent = 'Checking backend...';
  try {
    const payload = await fetchHealth();
    status.textContent = payload.ok ? 'Backend responded successfully.' : 'Backend returned an unexpected payload.';
  } catch (error) {
    status.textContent = 'Backend is not running yet.';
  }
});
