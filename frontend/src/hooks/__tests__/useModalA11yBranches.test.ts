import { describe, it, expect, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useRef } from 'react';
import { useModalA11y } from '../useModalA11y';

describe('useModalA11y Hook Branch Coverage Suite', () => {
  it('returns early when isOpen is false', () => {
    const { result } = renderHook(() =>
      useModalA11y({ isOpen: false })
    );
    expect(result.current.containerRef.current).toBeNull();
  });

  it('focuses initialFocusRef when provided', async () => {
    const initialElement = document.createElement('button');
    document.body.appendChild(initialElement);
    const focusSpy = vi.spyOn(initialElement, 'focus');

    const container = document.createElement('div');
    document.body.appendChild(container);

    renderHook(() => {
      const initialRef = useRef<HTMLElement | null>(initialElement);
      const hook = useModalA11y({
        isOpen: true,
        initialFocusRef: initialRef,
      });
      hook.containerRef.current = container;
      return hook;
    });

    await new Promise((resolve) => requestAnimationFrame(resolve));
    expect(focusSpy).toHaveBeenCalled();

    document.body.removeChild(initialElement);
    document.body.removeChild(container);
  });

  it('focuses containerRef when zero focusable elements are present', async () => {
    const container = document.createElement('div');
    container.tabIndex = -1;
    document.body.appendChild(container);
    const focusSpy = vi.spyOn(container, 'focus');

    renderHook(() => {
      const hook = useModalA11y({ isOpen: true });
      hook.containerRef.current = container;
      return hook;
    });

    await new Promise((resolve) => requestAnimationFrame(resolve));
    expect(focusSpy).toHaveBeenCalled();
    document.body.removeChild(container);
  });

  it('handles Escape key without onClose safely', () => {
    const container = document.createElement('div');
    document.body.appendChild(container);

    renderHook(() => {
      const hook = useModalA11y({
        isOpen: true,
        closeOnEscape: true,
        onClose: undefined,
      });
      hook.containerRef.current = container;
      return hook;
    });

    const event = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true });
    window.dispatchEvent(event);
    document.body.removeChild(container);
  });

  it('handles Tab key when zero focusable elements remain in container', () => {
    const container = document.createElement('div');
    document.body.appendChild(container);

    renderHook(() => {
      const hook = useModalA11y({
        isOpen: true,
        trapFocus: true,
      });
      hook.containerRef.current = container;
      return hook;
    });

    const event = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true });
    const preventSpy = vi.spyOn(event, 'preventDefault');
    window.dispatchEvent(event);
    expect(preventSpy).toHaveBeenCalled();

    document.body.removeChild(container);
  });

  it('wraps focus on Shift+Tab from container and first element to last element', () => {
    const container = document.createElement('div');
    const btn1 = document.createElement('button');
    const btn2 = document.createElement('button');
    container.appendChild(btn1);
    container.appendChild(btn2);
    document.body.appendChild(container);

    const btn2FocusSpy = vi.spyOn(btn2, 'focus');

    renderHook(() => {
      const hook = useModalA11y({
        isOpen: true,
        trapFocus: true,
      });
      hook.containerRef.current = container;
      return hook;
    });

    btn1.focus();
    const event = new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true });
    window.dispatchEvent(event);
    expect(btn2FocusSpy).toHaveBeenCalled();

    document.body.removeChild(container);
  });

  it('wraps focus on Tab from last element to first element', () => {
    const container = document.createElement('div');
    const btn1 = document.createElement('button');
    const btn2 = document.createElement('button');
    container.appendChild(btn1);
    container.appendChild(btn2);
    document.body.appendChild(container);

    const btn1FocusSpy = vi.spyOn(btn1, 'focus');

    renderHook(() => {
      const hook = useModalA11y({
        isOpen: true,
        trapFocus: true,
      });
      hook.containerRef.current = container;
      return hook;
    });

    btn2.focus();
    const event = new KeyboardEvent('keydown', { key: 'Tab', shiftKey: false, bubbles: true });
    window.dispatchEvent(event);
    expect(btn1FocusSpy).toHaveBeenCalled();

    document.body.removeChild(container);
  });

  it('restores focus on trigger element when modal unmounts', async () => {
    const trigger = document.createElement('button');
    document.body.appendChild(trigger);
    trigger.focus();
    const triggerFocusSpy = vi.spyOn(trigger, 'focus');

    const { unmount } = renderHook(() =>
      useModalA11y({
        isOpen: true,
        restoreFocus: true,
      })
    );

    unmount();
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(triggerFocusSpy).toHaveBeenCalled();

    document.body.removeChild(trigger);
  });
});
