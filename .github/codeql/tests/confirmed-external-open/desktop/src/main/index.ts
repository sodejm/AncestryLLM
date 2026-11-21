import { openExternalLinkWithConfirmation } from './external-links';
declare const shell: any, dialog: any;
declare function externalLinkPrompt(value: string): unknown;

// GOOD: the production adapter resolves the protected wrapper across modules.
openExternalLinkWithConfirmation('https://example.test', {
  confirm: async (normalized: string) => {
    const response = await dialog.showMessageBox(externalLinkPrompt(normalized));
    return response.response === 1;
  },
  openExternal: async (normalized: string) => {
    await shell.openExternal(normalized);
  },
});

openExternalLinkWithConfirmation('https://example.test', {
  confirm: async (normalized: string) => {
    const response = await dialog.showMessageBox(externalLinkPrompt(normalized));
    return response.response === 0; // BAD: cancellation is treated as approval.
  },
  openExternal: async (normalized: string) => {
    await shell.openExternal(normalized); // BAD
  },
});
