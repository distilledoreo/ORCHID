import { resizeImage } from '@freetoshop/raster-engine';
import { importPsdBuffer } from '@freetoshop/file-codecs/psd';
import { decodeNativeDocument, encodeNativeDocument } from '@freetoshop/persistence';
import { createFilterPatches } from '../editor/filterOperations.js';
import { compositeWorkerResultBuffer } from './compositeWorkerTask.js';
import {
  createWorkerTaskDeserializationContext,
  createWorkerTaskSerializationContext,
  deserializeWorkerResult,
  deserializeWorkerTask,
  pruneWorkerTaskDeserializationContext,
  serializeWorkerResult,
  streamWorkerResultTiles,
  workerResultTransferables,
  type ImageWorkerEvent,
  type ImageWorkerRequest,
  type ImageWorkerResult,
} from './protocol.js';

const resourceContext = createWorkerTaskDeserializationContext();

const scope = self as unknown as {
  postMessage(message: ImageWorkerEvent, transfer?: Transferable[]): void;
  onmessage: ((event: MessageEvent<ImageWorkerRequest>) => void) | null;
};

scope.onmessage = (event) => {
  void processRequest(event);
};

async function processRequest(event: MessageEvent<ImageWorkerRequest>): Promise<void> {
  const { id } = event.data;
  const task = deserializeWorkerTask(event.data.task, resourceContext);
  if (event.data.retainResourceIds !== undefined) {
    pruneWorkerTaskDeserializationContext(resourceContext, event.data.retainResourceIds);
  }
  scope.postMessage({ id, type: 'progress', progress: 0.05 });
  try {
    let result: ImageWorkerResult;
    const postResult = (completed: ImageWorkerResult) => {
      const serialized = serializeWorkerResult(
        completed,
        createWorkerTaskSerializationContext(`result-${id}-`),
      );
      // Retain owned clones under the same collision-safe IDs before transfer
      // detaches the serialized result buffers.
      deserializeWorkerResult(serialized, resourceContext);
      if (
        completed.kind === 'resize' ||
        completed.kind === 'psd-import' ||
        completed.kind === 'native-import'
      ) {
        const streamProgressStart =
          completed.kind === 'native-import' ? 0.5 : completed.kind === 'psd-import' ? 0.6 : 0.05;
        const terminal = streamWorkerResultTiles(
          id,
          serialized,
          (message, transfer) => scope.postMessage(message, transfer),
          (completedTiles, totalTiles) => {
            scope.postMessage({
              id,
              type: 'progress',
              progress:
                streamProgressStart +
                (0.95 - streamProgressStart) * (completedTiles / Math.max(1, totalTiles)),
            });
          },
        );
        scope.postMessage({ id, type: 'progress', progress: 1 });
        scope.postMessage({ id, type: 'result', result: terminal });
        return;
      }
      scope.postMessage(
        { id, type: 'result', result: serialized },
        workerResultTransferables(serialized),
      );
    };
    if (task.kind === 'filter') {
      createFilterPatches(task.layer, task.selection, task.worldTransform, task.draft, {
        onPatch: (patch) => {
          const pixels = new Uint8ClampedArray(patch.pixels);
          scope.postMessage({ id, type: 'filter-patch', patch: { ...patch, pixels } }, [
            pixels.buffer,
          ]);
        },
        onProgress: (completed, total) => {
          scope.postMessage({
            id,
            type: 'progress',
            progress: 0.05 + 0.9 * (completed / Math.max(1, total)),
          });
        },
      });
      result = { kind: 'filter', patches: [] };
      scope.postMessage({ id, type: 'progress', progress: 1 });
      postResult(result);
      return;
    }
    if (task.kind === 'psd-import') {
      const imported = importPsdBuffer(task.buffer, {
        name: task.name,
        onProgress: ({ progress }) => {
          scope.postMessage({ id, type: 'progress', progress: 0.05 + 0.55 * progress });
        },
      });
      result = { kind: 'psd-import', ...imported };
      postResult(result);
      return;
    }
    if (task.kind === 'native-import') {
      const document = await decodeNativeDocument(task.buffer, {
        onProgress: (progress) => {
          scope.postMessage({ id, type: 'progress', progress: 0.05 + 0.45 * progress });
        },
      });
      result = { kind: 'native-import', document };
      postResult(result);
      return;
    }
    if (task.kind === 'native-export') {
      const encoded = await encodeNativeDocument(task.document);
      const buffer = encoded.buffer as ArrayBuffer;
      result = { kind: 'native-export', buffer };
      scope.postMessage({ id, type: 'progress', progress: 1 });
      postResult(result);
      return;
    }
    if (task.kind === 'composite') {
      scope.postMessage({ id, type: 'progress', progress: 0.2 });
      result = compositeWorkerResultBuffer(task.document, task.generation, {
        viewportRegion: task.viewportRegion,
        progressiveViewportOnly: task.progressiveViewportOnly,
        mipLevel: task.mipLevel,
      });
      scope.postMessage({ id, type: 'progress', progress: 1 });
      postResult(result);
      return;
    }
    const document = resizeImage({
      width: task.width,
      height: task.height,
      method: task.method,
    }).execute(task.document);
    result = { kind: 'resize', document };
    postResult(result);
  } catch (error) {
    scope.postMessage({
      id,
      type: 'error',
      message: error instanceof Error ? error.message : 'Worker operation failed.',
    });
  }
}
