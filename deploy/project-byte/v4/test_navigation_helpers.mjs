export async function openDock(page){
  if(await page.locator('#prfktNavToggle').getAttribute('aria-expanded')==='false')await page.locator('#prfktNavToggle').click();
}
