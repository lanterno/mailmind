// What the Reader offers. It comes last in the features' order, so no other feature can use it; app/ composes its files.
export { summarize } from "./AiCard";
export { Reader, readFocus, setReadFocus } from "./Reader";
