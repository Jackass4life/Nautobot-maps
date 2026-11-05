<script setup lang="ts">
const { items, pending, error, create, remove } = useItems()

const name = ref('')
const description = ref('')
const formError = ref<string | null>(null)

async function submit() {
  formError.value = await create({ name: name.value, description: description.value || null })
  if (!formError.value) {
    name.value = ''
    description.value = ''
  }
}
</script>

<template>
  <main>
    <h1>Items</h1>

    <form @submit.prevent="submit">
      <input v-model="name" placeholder="Name" required maxlength="200">
      <input v-model="description" placeholder="Description (optional)">
      <button type="submit">Add</button>
      <p v-if="formError" role="alert">{{ formError }}</p>
    </form>

    <p v-if="pending">Loading…</p>
    <p v-else-if="error" role="alert">{{ error.message }}</p>
    <p v-else-if="items.length === 0">No items yet.</p>
    <ul v-else>
      <li v-for="item in items" :key="item.id">
        <strong>{{ item.name }}</strong>
        <span v-if="item.description"> — {{ item.description }}</span>
        <small> (#{{ item.id }}, {{ new Date(item.created_at).toLocaleString() }})</small>
        <button type="button" @click="remove(item.id)">Delete</button>
      </li>
    </ul>
  </main>
</template>
