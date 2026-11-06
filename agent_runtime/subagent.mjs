/** 附加给 Pi 的委派工具；完整子上下文留在 AIpy，主模型直接验收并决定是否重做。 */
import {spawn} from 'node:child_process';
import {mkdirSync, writeFileSync, readFileSync, existsSync, copyFileSync} from 'node:fs';
import path from 'node:path';
import {Type} from '@sinclair/typebox';

async function executeWorker(request, task, directory, expectedFiles, signal) {
  return new Promise((resolve, reject) => {
    const child = spawn(request.python, ['-m', 'atlasforge.agents.aipy_worker'], {
      cwd: request.root, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
      env: {...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8'},
    });
    let stdout = '', stderr = '';
    const stop = () => {if (child.pid) spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], {windowsHide: true, stdio: 'ignore'});};
    signal?.addEventListener('abort', stop, {once: true});
    child.stdout.on('data', bytes => {stdout = (stdout + bytes.toString()).slice(-100000);});
    child.stderr.on('data', bytes => {stderr = (stderr + bytes.toString()).slice(-5000);});
    child.on('error', reject);
    child.on('close', code => {
      signal?.removeEventListener('abort', stop);
      if (signal?.aborted) reject(new Error('子任务已取消'));
      else if (code) reject(new Error(stderr || stdout || `AIpy exit ${code}`));
      else {try {resolve(JSON.parse(stdout));} catch {reject(new Error('AIpy 结果协议无效'));}}
    });
    child.stdin.end(JSON.stringify({task, directory, purpose: request.mode === 'draft' ? 'draft' : 'execute',
      expected_files: expectedFiles, config: request.config}));
  });
}

export function createDelegate(request, model, emit) {
  return {
    name: 'delegate_aipy', label: '官方 AIpy 子 Agent',
    description: '交给官方 AIpy 独立执行。只回传摘要和文件路径，完整上下文保存于子任务。反馈重做须传 task_id，恢复同一任务；检查真实产物后选用最佳版本。',
    parameters: Type.Object({
      task: Type.String({description: '任务或本次具体改进要求，含必要背景和输入文件绝对路径'}),
      task_id: Type.Optional(Type.String({description: '继续已有子任务；省略则新建'})),
      expected_files: Type.Optional(Type.Array(Type.String())),
    }),
    async execute(id, args, signal) {
      const taskId = args.task_id || Date.now() + '-' + Math.random().toString(16).slice(2, 8);
      if (!/^[a-zA-Z0-9-]+$/.test(taskId)) throw new Error('无效子任务 ID');
      const directory = path.join(request.root, 'data', 'tasks', taskId);
      const recordPath = path.join(directory, 'task.json');
      if (args.task_id && !existsSync(recordPath)) throw new Error('子任务不存在');
      mkdirSync(directory, {recursive: true});
      const record = existsSync(recordPath) ? JSON.parse(readFileSync(recordPath, 'utf8')) :
        {id: taskId, task: args.task, started: new Date().toISOString(), attempts: []};
      const retries = request.config.subagent_retries ?? 5;
      if (record.attempts.length >= retries + 1) throw new Error(`已达到最多 ${retries} 次重做；请评估已有版本并向老板报告`);
      const attempt = {number: record.attempts.length + 1, task: args.task, state: 'running'};
      record.attempts.push(attempt);
      record.state = 'running';
      const save = () => writeFileSync(recordPath, JSON.stringify(record, null, 2));
      save();
      emit({type: 'task', id: taskId, text: args.task});
      try {
        const work = path.join(directory, 'work');
        const report = await executeWorker(request, args.task, work, args.expected_files || [], signal);
        // 每轮产物保留快照，继续原任务不会覆盖已经可用的版本。
        for (let i = 0; i < report.artifacts.length; i++) {
          const artifact = report.artifacts[i];
          const retained = path.join(directory, `attempt-${attempt.number}`, 'retained', String(i), path.basename(artifact.path));
          mkdirSync(path.dirname(retained), {recursive: true});
          copyFileSync(artifact.path, retained);
          artifact.original_path = artifact.path;
          artifact.path = retained;
        }
        const state = report.task_status?.completed === false ? 'needs_attention' : 'completed';
        attempt.state = state;
        attempt.report = report;
        record.state = state;
        record.finished = new Date().toISOString();
        save();
        emit({type: 'task_end', id: taskId, state, directory});
        const brief = {task_id: taskId, attempt: attempt.number, remaining_redos: retries + 1 - record.attempts.length,
          summary: report.summary, task_status: report.task_status, artifacts: report.artifacts, full_result: path.join(work, 'full-result.txt'),
          verification: report.verification};
        return {content: [{type: 'text', text: JSON.stringify(brief)}], details: {directory, report, task_id: taskId}};
      } catch (error) {
        attempt.state = record.state = signal?.aborted ? 'cancelled' : 'failed';
        attempt.error = String(error); save();
        emit({type: 'task_end', id: taskId, state: record.state, directory});
        throw error;
      }
    },
  };
}
