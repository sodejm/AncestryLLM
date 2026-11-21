/** Exercises IPC authorization guards; marked unsafe cases are intentional fixtures. */
declare const ipc: any, authorize: any, schedule: any, unauthorized: any, flag: boolean;
declare const otherEvent: unknown, fileGrants: any;

ipc.handle('missing', async (event: unknown) => {
  return schedule(event); // BAD: no authorization
});
ipc.handle('ignored', async (event: unknown) => {
  authorize(event);
  return schedule(event); // BAD: ignored result
});
ipc.handle('wrong-event', async (event: unknown) => {
  const state = authorize(otherEvent);
  if (!state) return unauthorized();
  return schedule(state); // BAD: another event was authorized
});
ipc.handle('partial-branch', async (event: unknown) => {
  const state = authorize(event);
  if (flag && !state) return unauthorized();
  return schedule(state); // BAD: flag=false bypasses authorization
});
ipc.handle('late', async (event: unknown) => {
  fileGrants.requestOpenFileGrant(event); // BAD: native work precedes check
  const state = authorize(event);
  if (!state) return unauthorized();
  return schedule(state); // GOOD
});
ipc.handle('early-return', async (event: unknown) => {
  const alias = event;
  const state = authorize(alias);
  if (!state) return unauthorized();
  return schedule(state); // GOOD
});
ipc.handle('positive-branch', async (event: unknown) => {
  const state = authorize(event);
  if (state) return schedule(state); // GOOD
  return unauthorized();
});
function registerNoArgumentHandler(ipc: any, channel: string, authorize: any, operation: any) {
  ipc.handle(channel, async (event: unknown) => {
    const state = authorize(event);
    if (!state) return unauthorized();
    return schedule(state, operation); // GOOD: existing shared helper
  });
}
registerNoArgumentHandler(ipc, 'helper', authorize, () => undefined);
ipc.handle('conditional-event', async (event: unknown) => {
  const state = authorize(flag ? event : otherEvent);
  if (!state) return unauthorized();
  return schedule(state); // BAD: authorization may concern another event
});
ipc.handle('conditional-result', async (event: unknown) => {
  const state = authorize(event);
  if (!(flag ? state : true)) return unauthorized();
  return schedule(state); // BAD: success may be fabricated
});
