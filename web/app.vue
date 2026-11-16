<template>
  <UApp>
    <!-- The top bar is the layout's (#737): mounted once, the pages under it. -->
    <NuxtLayout>
      <NuxtPage />
    </NuxtLayout>
    <!-- The app's toasts (shadcn-vue's Sonner, #640): toast("…") from vue-sonner. -->
    <SSonner />
  </UApp>
</template>
