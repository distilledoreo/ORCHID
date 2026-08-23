import { useEffect, useRef, type ReactNode } from 'react';

export interface ModalDialogProps {
  readonly open: boolean;
  readonly title: string;
  readonly onCancel: () => void;
  readonly children: ReactNode;
}

export function ModalDialog({ open, title, onCancel, children }: ModalDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const headingId = `${title.toLowerCase().replaceAll(' ', '-')}-heading`;

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={dialogRef}
      className="document-dialog"
      aria-labelledby={headingId}
      onCancel={(event) => {
        event.preventDefault();
        onCancel();
      }}
    >
      <h2 id={headingId}>{title}</h2>
      {children}
    </dialog>
  );
}
