@tool
extends EditorPlugin

const ButtonScene := preload("uid://b04l8m8uoei07")
const DockScene := preload("uid://dj6m63g8ll2k")

var dock: EditorDock


func _enable_plugin() -> void:
	# Add autoloads here.
	pass


func _disable_plugin() -> void:
	# Remove autoloads here.
	pass


func _enter_tree() -> void:
	var button_scene := DockScene.instantiate()

	dock = EditorDock.new()
	dock.add_child(button_scene)

	dock.title = "My Dock"
	dock.default_slot = EditorDock.DOCK_SLOT_LEFT_UL
	dock.available_layouts = EditorDock.DOCK_LAYOUT_VERTICAL | EditorDock.DOCK_LAYOUT_FLOATING

	add_dock(dock)


func _exit_tree() -> void:
	remove_dock(dock)
	dock.queue_free()
