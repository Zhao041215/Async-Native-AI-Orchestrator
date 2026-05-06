export async function fetchHealth(baseUrl = 'http://127.0.0.1:9000') {
  const response = await fetch(`${baseUrl}/health`);
  if (!response.ok) {
    throw new Error(`Health check failed: ${response.status}`);
  }
  return response.json();
}

export async function fetchProjects(baseUrl = 'http://127.0.0.1:9000') {
  const response = await fetch(`${baseUrl}/api/projects`);
  if (!response.ok) {
    throw new Error(`Project fetch failed: ${response.status}`);
  }
  const payload = await response.json();
  return payload.items || [];
}
