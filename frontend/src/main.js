import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import App from './App.vue'
import router from './router'
import { setupPermissionDirective } from './directives/permission'
import './styles/global.css'

async function bootstrap() {
  // 离线演示 / 独立开发：VITE_USE_MOCK=true 时启用 MSW 拦截 /api/v1/**
  if (import.meta.env.VITE_USE_MOCK === 'true') {
    const { worker } = await import('./mocks/browser')
    await worker.start({ onUnhandledRequest: 'bypass', quiet: true })
  }

  const app = createApp(App)
  app.use(createPinia())
  app.use(router)
  app.use(ElementPlus, { locale: zhCn })
  setupPermissionDirective(app)
  app.mount('#app')
}

bootstrap()
