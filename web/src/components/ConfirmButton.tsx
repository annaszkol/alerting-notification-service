// A button that asks before doing something that cannot be undone (delete, reject).
import { Button, Group, Modal, Text, type ButtonProps } from "@mantine/core";
import { useState, type ReactNode } from "react";

interface Props extends ButtonProps {
  children: ReactNode;
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  onConfirm: () => void;
}

export function ConfirmButton({ children, title, message, confirmLabel = "Delete", onConfirm, ...button }: Props) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button color="red" variant="light" {...button} onClick={() => setOpen(true)}>
        {children}
      </Button>
      <Modal opened={open} onClose={() => setOpen(false)} title={title} centered>
        <Text size="sm">{message}</Text>
        <Group justify="end" mt="md">
          <Button variant="default" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            color="red"
            onClick={() => {
              setOpen(false);
              onConfirm();
            }}
          >
            {confirmLabel}
          </Button>
        </Group>
      </Modal>
    </>
  );
}
