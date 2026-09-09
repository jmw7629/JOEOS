import { chromium } from '/Users/user/PROJECT_BYTE-review-20260908/browser-tooling/node_modules/playwright/index.mjs'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import assert from 'node:assert/strict'
const root = '/Users/user/PROJECT_BYTE-review-20260908/private-install-preview-2'
const key = (await readFile(root + '/private/owner.key', 'utf8')).trim()
const output = '/Users/user/PROJECT_BYTE-review-20260908/evidence/private-install'
await mkdir(output, { recursive: true })
const browser = await chromium.launch({ headless: true, executablePath: '/Users/user/PROJECT_BYTE-review-20260908/browser-cache/chromium-1200/chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing' })
const evidence = []
try {
  for (const width of [390, 1440]) {
    const page = await browser.newPage({ viewport: { width, height: 1000 } })
    const errors = []
    page.on('pageerror', e => errors.push(e.message))
    await page.goto('http://127.0.0.1:4191/')
    await page.waitForFunction(() => document.title === 'Private Install Review')
    const anonymous = await page.request.get('http://127.0.0.1:4191/api/tasks')
    assert.equal(anonymous.status(), 401)
    page.once('dialog', dialog => dialog.accept(key))
    await page.locator('#login').click()
    await page.waitForFunction(() => document.querySelector('#login')?.textContent.includes('Reviewer'))
    await page.getByRole('button', { name: 'New work', exact: true }).click()
    await page.locator('#taskTitle').fill(`Private browser task ${width}`)
    await page.locator('#taskProject').fill('Disposable review')
    await page.locator('#taskForm').getByRole('button', { name: 'Save', exact: true }).click()
    await page.locator('#taskModal').waitFor({ state: 'hidden' })
    const tasks = await page.request.get('http://127.0.0.1:4191/api/tasks', { headers: { 'X-Access-Key': key } })
    assert.equal((await tasks.json()).tasks.some(t => t.title === `Private browser task ${width}`), true)
    await page.screenshot({ path: `${output}/home-${width}.png`, fullPage: true })
    const native = await page.request.get('http://127.0.0.1:4191/api/codex-workspace', { headers: { 'X-Access-Key': key } })
    assert.equal((await native.json()).configured, false)
    assert.deepEqual(errors, [])
    evidence.push({ width, brandedProfile: true, privateSignIn: true, createTask: true, executionUnconfigured: true, errors })
    await page.close()
  }
} finally { await browser.close() }
await writeFile(`${output}/browser.json`, JSON.stringify(evidence, null, 2) + '\n')
console.log(JSON.stringify(evidence))
