/** GUI 适配官方完整 Pi SDK；默认工具、系统提示、压缩和会话存储由上游负责。 */
import { createAgentSession, DefaultResourceLoader, ModelRuntime, SessionManager, SettingsManager } from '@earendil-works/pi-coding-agent';
import { mkdirSync, existsSync } from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import { createDelegate } from './subagent.mjs';

const emit = data => process.stdout.write(JSON.stringify(data) + '\n');
const input = readline.createInterface({input: process.stdin});
const line = await new Promise(resolve => input.once('line', resolve));
input.close();
const request = JSON.parse(line);
const {config, root} = request;
const agentDir = path.join(root, 'data', 'pi');
mkdirSync(agentDir, {recursive: true});
const runtime = await ModelRuntime.create({
  authPath: path.join(agentDir, 'auth.json'), modelsPath: path.join(agentDir, 'models.json'),
});
runtime.registerProvider('portable', {
  api: 'openai-completions', baseUrl: config.base_url, apiKey: config.api_key || 'local',
  models: [{id: config.model, name: config.model, reasoning: false,
    input: config.vision ? ['text', 'image'] : ['text'],
    cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0},
    contextWindow: config.context_size, maxTokens: Math.min(8192, Math.floor(config.context_size / 2)),
    compat: {supportsDeveloperRole: false, supportsStore: false}}],
});
const model = runtime.getModel(config.pi_provider || 'portable', config.model);
if (config.pi_provider && config.pi_provider !== 'portable' && config.api_key)
  runtime.setRuntimeApiKey(config.pi_provider, config.api_key);
if (!model) throw new Error('Pi 中没有找到指定模型，请检查 data/pi/models.json 和模型名称');
const delegate = createDelegate(request, model, emit);
let session;
try {
  if (request.mode === 'draft') {
    const result = await delegate.execute('draft', {task: request.prompt}, new AbortController().signal);
    emit({type: 'draft', text: result.details.report.summary});
    emit({type: 'done', messages: request.messages || [], session_file: request.session_file || null});
  } else {
    const sessionDir = path.join(agentDir, 'sessions');
    const sessionManager = request.session_file && existsSync(request.session_file)
      ? SessionManager.open(request.session_file, sessionDir, root) : SessionManager.create(root, sessionDir);
    // 旧 GUI 历史首次迁入；之后以官方 JSONL 会话为准。
    if (!request.session_file) for (const message of request.messages || []) {
      if (['user', 'assistant', 'toolResult'].includes(message.role)) sessionManager.appendMessage(message);
    }
    const settingsManager = SettingsManager.create(root, agentDir);
    const portableBash = path.join(root, 'runtime', 'git', 'bin', 'bash.exe');
    if (existsSync(portableBash)) settingsManager.applyOverrides({shellPath: portableBash});
    const resourceLoader = new DefaultResourceLoader({cwd: root, agentDir, settingsManager,
      appendSystemPrompt: [config.main_system_prompt || '',
        '可用 delegate_aipy 委派独立执行任务。提供必要背景和输入路径，避免转发整段历史。子任务仅回传摘要与产物路径，完整过程保存在任务目录。需要改进时通过 task_id 继续原子任务，最多重做配置允许的次数；检查产物后选择最合适的版本。']});
    await resourceLoader.reload();
    ({session} = await createAgentSession({cwd: root, agentDir, modelRuntime: runtime, model,
      settingsManager, resourceLoader, sessionManager, customTools: [delegate]}));
    emit({type: 'status', text: '官方 Pi · ' + session.getActiveToolNames().join(', ')});
    session.subscribe(event => {
      if (event.type === 'message_update' && event.assistantMessageEvent.type === 'text_delta')
        emit({type: 'delta', text: event.assistantMessageEvent.delta});
      if (event.type === 'tool_execution_start') emit({type: 'status', text: 'Pi 正在调用 ' + event.toolName});
      if (event.type === 'auto_compaction_start') emit({type: 'status', text: 'Pi 正在自动压缩上下文'});
    });
    await session.prompt(request.prompt, {images: request.images || []});
    const last = session.messages.filter(message => message.role === 'assistant').at(-1);
    if (last?.stopReason === 'error' || last?.stopReason === 'aborted') throw new Error(last.errorMessage || '请求中止');
    emit({type: 'done', messages: session.messages, session_file: sessionManager.getSessionFile()});
  }
} catch (error) { emit({type: 'error', text: String(error)}); process.exitCode = 1; }
finally { session?.dispose(); }
