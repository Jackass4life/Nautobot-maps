// Liveness for the container healthcheck (#577): no API call, so web stays
// healthy while the API restarts.
export default defineEventHandler(() => ({ status: 'ok' }))
