import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/login', name: 'login', component: () => import('../views/LoginView.vue') },
  {
    path: '/',
    name: 'kiosk',
    component: () => import('../views/KioskView.vue'),
    meta: { requiresAuth: true }
  },
  {
    path: '/lesson/:id',
    name: 'lesson',
    component: () => import('../views/LessonView.vue'),
    meta: { requiresAuth: true }
  },
  {
    path: '/teacher',
    component: () => import('../views/TeacherView.vue'),
    meta: { requiresAuth: true },
    children: [
      { path: '', redirect: '/teacher/weekly' },
      { path: 'weekly', name: 'teacher-weekly', component: () => import('../views/teacher/WeeklyReport.vue') },
      { path: 'content', name: 'teacher-content', component: () => import('../views/teacher/ContentStudio.vue') },
      { path: 'notifications', name: 'teacher-notifications', component: () => import('../views/teacher/NotificationsView.vue') },
      { path: 'approvals', name: 'teacher-approvals', component: () => import('../views/teacher/ApprovalsView.vue') },
      { path: 'monthly', name: 'teacher-monthly', component: () => import('../views/teacher/MonthlyReport.vue') }
    ]
  }
]

export const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach((to) => {
  const token = sessionStorage.getItem('fs_token')
  if (to.meta.requiresAuth && !token) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
})
