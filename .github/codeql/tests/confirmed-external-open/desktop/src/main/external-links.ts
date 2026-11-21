/** Exercises destination-bound confirmation; marked unsafe cases are intentional fixtures. */
declare const operations: any, shell: any, dialog: any, flag: boolean;
declare function validateExternalLink(value: string): string;
declare function externalLinkPrompt(value: string): unknown;

async function missing(value: string) {
  await shell.openExternal(value); // BAD
}
async function ignored(value: string) {
  const destination = validateExternalLink(value);
  await operations.confirm(destination);
  await operations.openExternal(destination); // BAD: ignored confirmation
}
async function wrongDestination(value: string, other: string) {
  const destination = validateExternalLink(value);
  if (!await operations.confirm(destination)) return;
  await operations.openExternal(other); // BAD
}
async function cancelled(value: string) {
  const destination = validateExternalLink(value);
  if (await operations.confirm(destination)) return;
  await operations.openExternal(destination); // BAD: opens after cancellation
}
async function branch(value: string) {
  const destination = validateExternalLink(value);
  if (flag && !await operations.confirm(destination)) return;
  await operations.openExternal(destination); // BAD: partial branch
}
async function unvalidated(value: string) {
  if (!await operations.confirm(value)) return;
  await operations.openExternal(value); // BAD: confirmation alone
}
export async function openExternalLinkWithConfirmation(value: string, operations: any) {
  const destination = validateExternalLink(value);
  if (!await operations.confirm(destination)) return { status: 'cancelled' };
  await operations.openExternal(destination); // GOOD: production wrapper
  return { status: 'opened' };
}
openExternalLinkWithConfirmation('https://example.test', {
  confirm: async (normalized: string) => {
    const response = await dialog.showMessageBox(externalLinkPrompt(normalized));
    return response.response === 1;
  },
  openExternal: async (normalized: string) => {
    await shell.openExternal(normalized); // GOOD: injected production adapter
  },
});
openExternalLinkWithConfirmation('https://example.test', {
  confirm: async (normalized: string) => {
    const response = await dialog.showMessageBox(externalLinkPrompt(normalized));
    return response.response === 1;
  },
  openExternal: async (normalized: string) => {
    await shell.openExternal('https://different.test'); // BAD: adapter destination differs
  },
});
openExternalLinkWithConfirmation('https://example.test', {
  confirm: async () => true,
  openExternal: async (normalized: string) => {
    await shell.openExternal(normalized); // BAD: confirmation adapter skips dialog
  },
});
async function conditionalDestination(value: string, other: string) {
  const destination = validateExternalLink(value);
  if (!await operations.confirm(destination)) return;
  await operations.openExternal(flag ? destination : other); // BAD: conditional replacement
}
async function conditionalConfirmation(value: string, other: string) {
  const destination = validateExternalLink(value);
  if (!await operations.confirm(flag ? destination : other)) return;
  await operations.openExternal(destination); // BAD: confirmation may concern another destination
}
openExternalLinkWithConfirmation('https://example.test', {
  confirm: async (normalized: string) => {
    const response = await dialog.showMessageBox(externalLinkPrompt(normalized));
    return response.response === 1;
  },
  openExternal: async (normalized: string) => {
    await shell.openExternal(flag ? normalized : 'https://other.test'); // BAD: adapter replacement
  },
});
