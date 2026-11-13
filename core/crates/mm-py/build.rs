//! The linker flags an extension module needs on macOS, which maturin passes too, so a plain `cargo build` or
//! `cargo test --workspace` links the library without a libpython.

fn main() {
    pyo3_build_config::add_extension_module_link_args();
}
