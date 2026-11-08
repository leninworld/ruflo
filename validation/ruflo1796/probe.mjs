// Diagnostic only. Run inside the separately approved offline Docker envelope.
// Reuses upstream discovery unchanged; never calls CLI.run or a Command action.
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { pathToFileURL } from 'node:url';

const [rootArg, reportArg] = process.argv.slice(2);
if (!rootArg || !reportArg || process.argv.length !== 4) {
  throw new Error('Usage: probe.mjs SOURCE_ROOT REPORT_JSON');
}
const root = resolve(rootArg);
const cli = join(root, 'v3/@claude-flow/cli');
const report = { node: process.version, arch: process.arch, errors: [], modules: [],
  commands: [], aliasCollisions: [], deepPaths: [], actionsInvoked: 0, harnessError: false };
const isCommand = value => value && typeof value === 'object'
  && typeof value.name === 'string' && typeof value.description === 'string';
const errorRecord = error => ({ name: error?.name, message: error?.message,
  stack: String(error?.stack || error).slice(0, 16384), code: error?.code });
const fail = (scope, error) => report.errors.push({ scope, ...errorRecord(error) });

try {
  const helper = await import(pathToFileURL(join(root, 'scripts/audit-skill-cli-commands.mjs')));
  const files = helper.topLevelCommandFiles(root);
  if (!files.size) throw new Error('Empty upstream command-file inventory');
  // No silent discovery drift: every lazy map entry must be accounted for by
  // the unchanged upstream discovery helper; actual exports provide a cross-check.
  const source = readFileSync(join(cli, 'src/commands/index.ts'), 'utf8');
  const mapBody = source.match(/const commandLoaders:[\s\S]*?=\s*\{([\s\S]*?)\n\};/);
  if (!mapBody) throw new Error('Missing commandLoaders source declaration');
  const imports = [...mapBody[1].matchAll(/import\('\.\/([^']+)\.js'\)/g)];
  if (!imports.length || imports.length > files.size) {
    throw new Error('Unchanged upstream path discovery is incomplete');
  }
  let registry;
  try {
    registry = await import(pathToFileURL(join(cli, 'dist/src/commands/index.js')));
    if (!Array.isArray(registry.commands) || typeof registry.getLazyCommandNames !== 'function') {
      throw new Error('Unexpected built registry API');
    }
    const actualKeys = new Set([...registry.getLazyCommandNames(), ...registry.commands.map(c => c.name)]);
    for (const name of actualKeys) {
      if (!files.has(name)) throw new Error(`Upstream discovery omitted ${name}`);
    }
  } catch (error) { fail('registry', error); }
  const canonical = new Map();
  for (const [key, file] of files) {
    try {
      const module = await import(pathToFileURL(join(cli, 'dist/src/commands', file + '.js')));
      const values = [...new Set(Object.values(module).filter(isCommand))];
      const matches = values.filter(c => c.name === key || c.aliases?.includes(key));
      // Match actual upstream loadCommand precedence, retaining exact-name
      // findings separately. A measurement finding is not a probe crash.
      const registered = module.default || module[key + 'Command'] || values[0];
      const resolution = module.default ? 'default' : module[key + 'Command'] ? 'named' : 'first-command-export';
      const status = !isCommand(registered) ? 'invalid-command'
        : matches.length > 1 ? 'ambiguous'
        : !matches.includes(registered) ? 'name-mismatch' : 'resolved';
      report.modules.push({ key, file, status, resolution,
        registeredName: registered?.name, exactMatches: matches.map(c => c.name),
        exportedCommandNames: values.map(c => c.name) });
      if (status !== 'resolved') fail(key, new Error(`Command resolution finding: ${status}`));
      if (!isCommand(registered)) continue;
      const command = registered;
      if (canonical.has(command.name) && canonical.get(command.name) !== command) {
        throw new Error(`Different command objects share name ${command.name}`);
      }
      canonical.set(command.name, command);
    } catch (error) {
      fail(key, error);
      report.modules.push({ key, file, status: 'import-error', error: errorRecord(error) });
    }
  }
  if (registry) {
    for (const command of registry.commands) {
      if (!isCommand(command) || canonical.get(command.name) !== command) {
        fail('sync-union', new Error(`Unresolved or different sync command ${command?.name}`));
      }
    }
  }
  const ancestors = new Set();
  function walkSiblings(commands, parent = []) {
    const names = new Map();
    for (const command of commands) {
      if (!isCommand(command)) { fail(parent.join(' '), new Error('Invalid Command')); continue; }
      const path = [...parent, command.name];
      for (const name of [command.name, ...(command.aliases || [])]) {
        if (names.has(name) && names.get(name) !== command.name) {
          report.aliasCollisions.push({ parent, alias: name, commands: [names.get(name), command.name] });
        }
        names.set(name, command.name);
      }
      if (ancestors.has(command)) { fail(path.join(' '), new Error('Cyclic command tree')); continue; }
      ancestors.add(command);
      report.commands.push({ path, aliases: command.aliases || [], hidden: !!command.hidden,
        optionCount: command.options?.length || 0, hasAction: typeof command.action === 'function' });
      // A static warning, not proof of unreachable behavior: runtime resolver
      // supports arbitrary depth. Do not execute actions to resolve that question.
      if (path.length > 4) report.deepPaths.push({ path, status: 'REQUIRES_ARGV_RESOLUTION_AUDIT' });
      walkSiblings(command.subcommands || [], path);
      ancestors.delete(command);
    }
  }
  walkSiblings([...canonical.values()]);
  report.maximumDepth = Math.max(0, ...report.commands.map(c => c.path.length));
  report.complete = report.errors.length === 0 && report.aliasCollisions.length === 0;
} catch (error) { fail('probe', error); report.complete = false; report.harnessError = true; }
finally {
  report.activeResources = process.getActiveResourcesInfo();
  writeFileSync(resolve(reportArg), JSON.stringify(report, null, 2) + '\n', { flag: 'wx' });
}
process.exit(report.harnessError ? 1 : report.complete ? 0 : 2);
