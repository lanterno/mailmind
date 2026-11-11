// A hook run as a component runs it, for a Vitest suite, which has no DOM. React renders into a stand-in root: a component
// that renders nothing asks no more of a page than this, and whatever the hook itself reaches (document, history, an
// EventSource) the test stubs. Only tests import this; nothing the page loads does.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, createElement, type FunctionComponent, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { vi } from "vitest";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// What React reads of a window while it renders nothing, as an EventTarget, so a test can dispatch to it what a browser
// would (popstate, say).
function ensureWindow() {
  if (typeof window !== "undefined") return;
  vi.stubGlobal("window", Object.assign(new EventTarget(), { event: undefined, HTMLIFrameElement: class {} }));
}

// What React reads of the element it renders into, when it renders nothing into it.
const standInRoot = () => ({
  nodeType: 1, tagName: "DIV", namespaceURI: "http://www.w3.org/1999/xhtml", ownerDocument: null,
  addEventListener() {}, removeEventListener() {},
}) as unknown as Element;

// A client that gives up at once, as a test wants to see a failure rather than wait out the retries.
export const testClient = () => new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });

export interface Rendered<P, R> {
  readonly result: R;                        // what the hook gave on its last render
  rerender(props: P): Promise<void>;
  unmount(): Promise<void>;
}

interface ProbeProps<P, R> { hook: (props: P) => R; props: P; out: { value?: R } }

// Lowercase, so the compiler leaves it be: it would otherwise keep what `hook` gave and skip the hooks inside it.
function probe<P, R>({ hook, props, out }: ProbeProps<P, R>) {
  out.value = hook(props);
  return null;
}

const live = new Set<Root>();

export async function renderHook<R, P = undefined>(hook: (props: P) => R, options: { props?: P; client?: QueryClient } = {}): Promise<Rendered<P, R>> {
  ensureWindow();
  const out: { value?: R } = {};
  const root = createRoot(standInRoot());
  live.add(root);
  const tree = (props: P): ReactNode => {
    const el = createElement(probe as FunctionComponent<ProbeProps<P, R>>, { hook, props, out });
    return options.client ? createElement(QueryClientProvider, { client: options.client }, el) : el;
  };
  await act(async () => root.render(tree(options.props as P)));
  return {
    get result() {
      return out.value as R;
    },
    rerender: async (props) => act(async () => root.render(tree(props))),
    unmount: async () => {
      live.delete(root);
      await act(async () => root.unmount());
    },
  };
}

// Every hook still mounted, unmounted: a test's afterEach calls it before it takes its globals back.
export async function cleanup() {
  for (const root of [...live]) {
    live.delete(root);
    await act(async () => root.unmount());
  }
}

// Whatever was set off since, settled inside React's turn: a fetch answered, a timer run.
export const settle = () => act(async () => {});
