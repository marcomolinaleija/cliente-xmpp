// Same descriptor code generation as the pinned reference's wacore/build.rs.
fn main() -> Result<(), Box<dyn std::error::Error>> {
    buffa_build::Config::new()
        .descriptor_set("mlow/tables.desc")
        .files(&["tables.proto"])
        .preserve_unknown_fields(false)
        .out_dir(std::env::var("OUT_DIR")?)
        .compile()
}
