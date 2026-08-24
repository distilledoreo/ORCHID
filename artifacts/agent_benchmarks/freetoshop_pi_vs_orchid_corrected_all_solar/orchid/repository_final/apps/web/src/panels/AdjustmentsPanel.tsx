import { ADJUSTMENT_LAYER_COMMAND_IDS } from '../editor/operationDefinitions.js';
import type { CommandRegistry } from '../commands/commandTypes.js';
import { executeCommand } from '../commands/commandRegistry.js';
import { Icon } from '../ui/Icon.js';

export function AdjustmentsPanel({ registry }: { readonly registry: CommandRegistry }) {
  return (
    <section className="panel-section adjustments-panel" aria-labelledby="adjustments-heading">
      <header className="panel-section-heading">
        <h2 id="adjustments-heading">Adjustments</h2>
        <p>Create nondestructive adjustment layers.</p>
      </header>
      <div className="adjustment-command-grid">
        {ADJUSTMENT_LAYER_COMMAND_IDS.map((id) => {
          const command = registry.get(id);
          if (!command) return null;
          const label = command.label.replace(/…$/, '');
          return (
            <button
              key={id}
              className="action-button adjustment-command-button"
              type="button"
              aria-label={command.label}
              title={command.label}
              disabled={!command.enabled}
              onClick={() => executeCommand(registry, id)}
            >
              <Icon name="adjustment" />
              <span>{label}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
