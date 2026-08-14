import { useEffect, useRef } from 'react';

interface UseModalA11yOptions {
  isOpen: boolean;
  onClose?: () => void;
  initialFocusRef?: React.RefObject<HTMLElement | null>;
  closeOnEscape?: boolean;
  trapFocus?: boolean;
  restoreFocus?: boolean;
}

const FOCUSABLE_SELECTOR =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"]):not([disabled])';

/**
 * Custom hook for accessible modal / dialog / drawer management.
 * Features:
 * 1. Automatically saves previous active element before opening.
 * 2. Focuses initial element or first focusable element inside container on open.
 * 3. Enforces strict focus trap (Tab and Shift+Tab wrap within container).
 * 4. Listens for Escape key to trigger onClose.
 * 5. Restores focus to the triggering element upon closing.
 */
export function useModalA11y<T extends HTMLElement = HTMLDivElement>({
  isOpen,
  onClose,
  initialFocusRef,
  closeOnEscape = true,
  trapFocus = true,
  restoreFocus = true,
}: UseModalA11yOptions) {
  const containerRef = useRef<T | null>(null);
  const triggerElementRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!isOpen) return;

    // 1. Capture current focused element before opening modal
    if (typeof document !== 'undefined') {
      triggerElementRef.current = document.activeElement as HTMLElement | null;
    }

    // 2. Focus initial or first focusable element
    const frameId = requestAnimationFrame(() => {
      if (!containerRef.current) return;

      if (initialFocusRef?.current) {
        initialFocusRef.current.focus();
        return;
      }

      const focusable = containerRef.current.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR);
      if (focusable.length > 0) {
        focusable[0]?.focus();
      } else {
        containerRef.current.focus();
      }
    });

    // 3. Keydown handler for Escape & Focus Trapping
    const handleKeyDown = (event: KeyboardEvent) => {
      if (!containerRef.current) return;

      // Handle Escape Key
      if (event.key === 'Escape' && closeOnEscape && onClose) {
        event.preventDefault();
        event.stopPropagation();
        onClose();
        return;
      }

      // Handle Tab Key Focus Trap
      if (event.key === 'Tab' && trapFocus) {
        const focusableElements = Array.from(
          containerRef.current.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)
        ).filter(
          (el) =>
            !el.hasAttribute('disabled') &&
            el.getAttribute('aria-hidden') !== 'true' &&
            el.getAttribute('tabindex') !== '-1' &&
            el.style.display !== 'none' &&
            el.style.visibility !== 'hidden'
        );

        if (focusableElements.length === 0) {
          event.preventDefault();
          return;
        }

        const firstElement = focusableElements[0];
        const lastElement = focusableElements[focusableElements.length - 1];

        if (event.shiftKey) {
          // Shift + Tab: if on first element, wrap to last
          if (document.activeElement === firstElement || document.activeElement === containerRef.current) {
            event.preventDefault();
            lastElement?.focus();
          }
        } else {
          // Tab: if on last element, wrap to first
          if (document.activeElement === lastElement) {
            event.preventDefault();
            firstElement?.focus();
          }
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown, true);

    // 4. Cleanup and focus restoration
    return () => {
      cancelAnimationFrame(frameId);
      window.removeEventListener('keydown', handleKeyDown, true);

      if (restoreFocus && triggerElementRef.current && typeof triggerElementRef.current.focus === 'function') {
        // Small delay to allow react unmount transitions
        setTimeout(() => {
          triggerElementRef.current?.focus();
        }, 10);
      }
    };
  }, [isOpen, onClose, initialFocusRef, closeOnEscape, trapFocus, restoreFocus]);

  return {
    containerRef,
    triggerElementRef,
  };
}
