# Where the old root suites' tests live

The refactor began with 13 suites at the repo root (502c9b3). Their 277 test functions and two linear scripts are all
still run, and this lists where each now lives. A name in the second column is a new name for the same check;
the old suites' other functions kept theirs. Generated once, when the tests moved into this tree; it records that
move and isn't kept up as tests change.

Three cases of `test_claude_api.py` were about the `claude` command as an engine, which decision D8 retired. What they
guarded became the checks that with no key AI is off and nothing is sent.

## `test_claude_api.py`

| Test | Now |
|---|---|
| `test_request_carries_key_version_and_fallback` | `tests/contract/test_ai_contract.py` `test_a_request_carries_the_key_the_version_and_the_refusal_fallback` |
| `test_schema_goes_out_closed_and_without_bounds` | `tests/contract/test_ai_contract.py` `test_the_schema_goes_out_closed_and_without_bounds` |
| `test_nested_objects_are_closed_and_property_names_kept` | `tests/contract/test_ai_contract.py` |
| `test_triage_runs_at_low_effort_and_the_rest_at_medium` | `tests/contract/test_ai_contract.py` |
| `test_model_override_and_models_without_effort_or_fallback` | `tests/contract/test_ai_contract.py` `test_a_model_override_and_the_models_without_effort_or_fallback` |
| `test_no_schema_mode_sends_no_format_and_parses_loose_json` | `tests/contract/test_ai_contract.py` `test_without_schemas_no_format_goes_out_and_loose_json_is_read` |
| `test_triage_stores_the_structured_reply` | `tests/contract/test_ai_contract.py` |
| `test_plain_text_call_returns_the_text` | `tests/contract/test_ai_contract.py` `test_a_plain_text_call_returns_the_text` |
| `test_refusal_reads_as_an_empty_reply` | `tests/contract/test_ai_contract.py` `test_a_refusal_reads_as_an_empty_reply` |
| `test_only_the_answer_after_a_fallback_block_counts` | `tests/contract/test_ai_contract.py` |
| `test_rate_limit_is_retried` | `tests/contract/test_ai_contract.py` `test_a_rate_limit_is_retried` |
| `test_bad_key_raises_without_retry` | `tests/contract/test_ai_contract.py` `test_a_bad_key_raises_without_a_retry_and_without_the_key` |
| `test_failed_triage_is_left_for_the_next_run` | `tests/contract/test_ai_contract.py` `test_a_failed_triage_is_left_for_the_next_run` |
| `test_persistent_overload_gives_up_after_two_retries` | `tests/contract/test_ai_contract.py` `test_a_persistent_overload_gives_up_after_two_retries` |
| `test_unreachable_api_raises` | `tests/contract/test_ai_contract.py` `test_an_unreachable_api_raises` |
| `test_engine_follows_the_key_unless_told_otherwise` | `tests/contract/test_ai_contract.py` `test_ai_is_on_with_a_key_and_off_without_one` |
| `test_a_key_in_the_config_file_wins_over_the_environment` | `tests/contract/test_ai_contract.py` |
| `test_a_config_key_that_cannot_be_read_is_no_key` | `tests/contract/test_ai_contract.py` |
| `test_status_names_the_engine_model_and_key_source` | `tests/contract/test_ai_contract.py` `test_status_names_the_engine_the_model_and_the_key_source` |
| `test_status_records_a_rejected_key_then_a_good_call` | `tests/contract/test_ai_contract.py` |
| `test_status_without_a_key_follows_the_claude_command` | `tests/contract/test_ai_contract.py` `test_without_a_key_nothing_is_sent_and_nothing_counts_as_failed` |
| `test_a_forced_api_engine_without_a_key_is_none` | `tests/contract/test_ai_contract.py` `test_without_a_key_nothing_is_sent_and_nothing_counts_as_failed` |

## `test_contacts_known.py`: `tests/unit/discovery/script_contacts_known.py`

| Test | Now |
|---|---|
| `test_received_non_junk_only` | `tests/unit/discovery/script_contacts_known.py` |
| `test_provider_spam_folders_never_vouch` | `tests/unit/discovery/script_contacts_known.py` |
| `test_own_sent_rows_count_every_recipient` | `tests/unit/discovery/script_contacts_known.py` |
| `test_exact_tokens_not_substrings` | `tests/unit/discovery/script_contacts_known.py` |
| `test_truncated_recipients_drop_the_partial_tail` | `tests/unit/mailbox/test_message_recipients.py` `test_the_last_token_of_a_column_that_was_cut_is_left_out` |
| `test_drafts_never_vouch` | `tests/unit/discovery/script_contacts_known.py` |
| `test_send_as_identity_in_a_synced_sent_folder` | `tests/unit/discovery/script_contacts_known.py` |
| `test_outbox_counts_only_delivered` | `tests/unit/discovery/script_contacts_known.py` |
| `test_idna_forms_match_both_ways` | `tests/unit/discovery/script_contacts_known.py` |
| `test_keep_senders_count_keep_domains_do_not` | `tests/unit/discovery/script_contacts_known.py` |
| `test_own_addresses_are_self` | `tests/unit/discovery/script_contacts_known.py` |
| `test_input_shape_and_limits` | `tests/unit/discovery/script_contacts_known.py` |
| `test_thin_history_flag` | `tests/unit/discovery/script_contacts_known.py` |
| `test_the_delivered_address_is_judged_not_the_display_name` | `tests/unit/discovery/script_contacts_known.py` |
| `test_items_carry_only_known_and_self` | `tests/unit/discovery/script_contacts_known.py` |
| `test_route_requires_csrf` | `tests/unit/discovery/script_contacts_known.py` |
| `test_never_waits_on_the_imap_lock` | `tests/unit/discovery/script_contacts_known.py` |

## `test_css_sanitizer.py`: `tests/unit/content/test_css_sanitizer.py`

| Test | Now |
|---|---|
| `test_escaped_url_keyword_is_gated` | `tests/unit/content/test_css_sanitizer.py` `test_escaped_url_is_gated[escaped_url_keyword]` |
| `test_escaped_url_paren_is_gated` | `tests/unit/content/test_css_sanitizer.py` `test_escaped_url_is_gated[escaped_url_paren]` |
| `test_split_url_letters_decoded` | `tests/unit/content/test_css_sanitizer.py` `test_escaped_url_is_gated[split_url_letters]` |
| `test_residual_backslash_after_decode_drops` | `tests/unit/content/test_css_sanitizer.py` |
| `test_emit_copy_equals_match_copy_no_reexpand` | `tests/unit/content/test_css_sanitizer.py` |
| `test_multi_url_data_plus_remote_drops_entire_decl` | `tests/unit/content/test_css_sanitizer.py` |
| `test_remote_url_blocked_counts_once` | `tests/unit/content/test_css_sanitizer.py` |
| `test_data_svg_url_dropped` | `tests/unit/content/test_css_sanitizer.py` `test_dropped_even_with_consent[data_svg_url]` |
| `test_javascript_url_dropped` | `tests/unit/content/test_css_sanitizer.py` `test_dropped_even_with_consent[javascript_url]` |
| `test_data_png_url_kept_without_consent` | `tests/unit/content/test_css_sanitizer.py` |
| `test_remote_url_kept_with_consent` | `tests/unit/content/test_css_sanitizer.py` |
| `test_url_on_nonurl_property_dropped` | `tests/unit/content/test_css_sanitizer.py` `test_dropped_even_with_consent[url_on_nonurl_property]` |
| `test_expression_dropped` | `tests/unit/content/test_css_sanitizer.py` `test_dropped[expression]` |
| `test_behavior_dropped` | `tests/unit/content/test_css_sanitizer.py` `test_dropped[behavior]` |
| `test_moz_binding_dropped` | `tests/unit/content/test_css_sanitizer.py` `test_dropped[moz_binding]` |
| `test_angle_bracket_in_value_dropped` | `tests/unit/content/test_css_sanitizer.py` `test_dropped[angle_bracket_in_value]` |
| `test_var_function_dropped` | `tests/unit/content/test_css_sanitizer.py` `test_dropped_even_with_consent[var_function]` |
| `test_position_fixed_dropped` | `tests/unit/content/test_css_sanitizer.py` `test_dropped[position_fixed]` |
| `test_position_absolute_dropped` | `tests/unit/content/test_css_sanitizer.py` `test_dropped[position_absolute]` |
| `test_position_escaped_fixed_dropped` | `tests/unit/content/test_css_sanitizer.py` `test_dropped[position_escaped_fixed]` |
| `test_position_relative_kept` | `tests/unit/content/test_css_sanitizer.py` |
| `test_viewport_height_curtain_dropped` | `tests/unit/content/test_css_sanitizer.py` `test_dropped[viewport_height_curtain], test_dropped[viewport_min_height_curtain]` |
| `test_percent_height_curtain_dropped` | `tests/unit/content/test_css_sanitizer.py` `test_dropped[percent_height_curtain]` |
| `test_zindex_dropped` | `tests/unit/content/test_css_sanitizer.py` `test_dropped[zindex]` |
| `test_transform_dropped` | `tests/unit/content/test_css_sanitizer.py` `test_dropped[transform]` |
| `test_import_rule_dropped` | `tests/unit/content/test_css_sanitizer.py` |
| `test_font_face_dropped` | `tests/unit/content/test_css_sanitizer.py` |
| `test_style_breakout_neutralized` | `tests/unit/content/test_css_sanitizer.py` |
| `test_media_query_recurses_and_keeps_safe` | `tests/unit/content/test_css_sanitizer.py` |
| `test_media_with_url_prelude_dropped` | `tests/unit/content/test_css_sanitizer.py` |
| `test_keyframes_dropped` | `tests/unit/content/test_css_sanitizer.py` |
| `test_selector_with_angle_bracket_drops_rule` | `tests/unit/content/test_css_sanitizer.py` |
| `test_oversized_inline_dropped_fast` | `tests/unit/content/test_css_sanitizer.py` |
| `test_unterminated_comment_no_hang` | `tests/unit/content/test_css_sanitizer.py` |
| `test_unterminated_brace_no_hang` | `tests/unit/content/test_css_sanitizer.py` |
| `test_newsletter_inline_styles_survive` | `tests/unit/content/test_css_sanitizer.py` |
| `test_newsletter_data_image_hero_survives` | `tests/unit/content/test_css_sanitizer.py` |
| `test_newsletter_stylesheet_survives` | `tests/unit/content/test_css_sanitizer.py` |
| `test_font_casing_preserved` | `tests/unit/content/test_css_sanitizer.py` |
| `test_text_shadow_and_box_shadow_kept` | `tests/unit/content/test_css_sanitizer.py` |

## `test_draft_resume.py`: `tests/unit/compose/script_draft_resume.py`

| Test | Now |
|---|---|
| `test_own_html_is_where_reply_and_forward_html_start` | `tests/unit/compose/script_draft_resume.py` |
| `test_quote_tail_of_fresh_reply_and_forward` | `tests/unit/compose/script_draft_resume.py` |
| `test_recorded_tail_survives_a_real_round_trip` | `tests/unit/compose/script_draft_resume.py` |
| `test_resave_three_times_is_stable` | `tests/unit/compose/script_draft_resume.py` |
| `test_exact_uses_the_recorded_text_even_without_a_text_part` | `tests/unit/compose/script_draft_resume.py` |
| `test_changed_quote_falls_back_to_whole` | `tests/unit/compose/script_draft_resume.py` |
| `test_pasted_quote_cuts_only_the_last_copy` | `tests/unit/compose/script_draft_resume.py` |
| `test_whole_drops_one_trailing_newline` | `tests/unit/compose/script_draft_resume.py` |
| `test_no_text_and_not_exact_is_none` | `tests/unit/compose/script_draft_resume.py` |
| `test_addr_chip_round_trips` | `tests/unit/compose/script_draft_resume.py` |
| `test_thread_ids_validate_dedupe_and_cap` | `tests/unit/compose/script_draft_resume.py` |
| `test_from_change_never_passes_the_old_uid` | `tests/unit/compose/script_draft_resume.py` |
| `test_from_change_deletes_the_old_copy_in_its_own_account` | `tests/unit/compose/script_draft_resume.py` |
| `test_from_change_then_two_saves_keeps_the_row_in_step` | `tests/unit/compose/script_draft_resume.py` |
| `test_from_change_removes_the_old_copy_only_after_the_new_one_is_saved` | `tests/unit/compose/script_draft_resume.py` |
| `test_failed_save_under_a_new_from_keeps_the_old_copy` | `tests/unit/compose/script_draft_resume.py` |
| `test_copy_in_another_drafts_folder_is_removed_there` | `tests/unit/compose/script_draft_resume.py` |
| `test_received_mail_in_a_drafts_named_folder_is_never_removed` | `tests/unit/compose/script_draft_resume.py` `test_received_mail_a_row_points_at_is_never_removed`; `tests/characterization/test_hwf_mailio.py` `test_hwf_received_mail_in_a_folder_named_for_drafts_is_no_draft` |
| `test_a_copy_mailmind_saved_is_removed_even_unflagged` | `tests/unit/compose/script_draft_resume.py` |
| `test_append_to_drafts_leaves_drafts_alone_for_a_copy_elsewhere` | `tests/unit/compose/script_draft_resume.py` |
| `test_unvouched_old_copy_is_left_alone` | `tests/unit/compose/script_draft_resume.py` |
| `test_other_apps_draft_registers_with_folder_uidvalidity` | `tests/unit/compose/script_draft_resume.py` |
| `test_vanished_draft_row_is_tracked_again` | `tests/unit/compose/script_draft_resume.py` |
| `test_migration_20_is_idempotent` | `tests/unit/compose/script_draft_resume.py` |
| `test_v19_database_gains_the_columns` | `tests/unit/compose/script_draft_resume.py` |
| `test_new_draft_records_its_text` | `tests/unit/compose/script_draft_resume.py` |
| `test_reply_draft_records_text_quote_and_html` | `tests/unit/compose/script_draft_resume.py` |
| `test_forward_draft_records_the_banner` | `tests/unit/compose/script_draft_resume.py` |
| `test_bcc_is_kept_beside_the_draft_not_in_it` | `tests/unit/compose/script_draft_resume.py` |
| `test_reply_draft_reopens_with_quote_files_bcc_and_thread` | `tests/unit/compose/script_draft_resume.py` |
| `test_forward_draft_reopens_with_its_files_as_chips` | `tests/unit/compose/script_draft_resume.py` |
| `test_original_deleted_still_reopens` | `tests/unit/compose/script_draft_resume.py` |
| `test_original_renumbered_found_by_message_id` | `tests/unit/compose/script_draft_resume.py` |
| `test_raw_sha_proves_the_version_without_a_uid` | `tests/unit/compose/script_draft_resume.py` |
| `test_changed_copy_uses_the_recorded_tail_and_drops_bcc` | `tests/unit/compose/script_draft_resume.py` |
| `test_copy_resaved_elsewhere_is_the_one_the_next_save_replaces` | `tests/unit/compose/script_draft_resume.py` |
| `test_record_of_a_replaced_copy_is_never_taken_for_the_new_one` | `tests/unit/compose/script_draft_resume.py` |
| `test_reply_resaved_elsewhere_keeps_its_recorded_quote` | `tests/unit/compose/script_draft_resume.py` |
| `test_a_save_that_moved_the_row_meanwhile_is_not_re_pointed_over` | `tests/unit/compose/script_draft_resume.py` |
| `test_same_bytes_without_a_uid_are_tracked_at_the_uid_they_got` | `tests/unit/compose/script_draft_resume.py` |
| `test_other_apps_draft_opens_whole_as_plain_text` | `tests/unit/compose/script_draft_resume.py` |
| `test_many_files_come_back_in_order_from_one_parse` | `tests/unit/compose/script_draft_resume.py` |
| `test_html_only_draft_is_refused` | `tests/unit/compose/script_draft_resume.py` |
| `test_attached_email_is_refused` | `tests/unit/compose/script_draft_resume.py` |
| `test_text_after_a_file_is_refused_not_dropped` | `tests/unit/compose/script_draft_resume.py` |
| `test_nameless_file_is_refused_unless_marked_an_attachment` | `tests/unit/compose/script_draft_resume.py` |
| `test_empty_extra_text_is_no_loss` | `tests/unit/compose/script_draft_resume.py` |
| `test_empty_first_text_cannot_hide_a_second` | `tests/unit/compose/script_draft_resume.py` |
| `test_apple_rich_text_draft_with_a_file_reopens_as_plain_text` | `tests/unit/compose/script_draft_resume.py` |
| `test_html_beside_the_text_with_words_of_its_own_is_refused` | `tests/unit/compose/script_draft_resume.py` |
| `test_outlook_style_invite_is_refused_a_named_one_is_a_file` | `tests/unit/compose/script_draft_resume.py` |
| `test_a_file_typed_multipart_is_saved_and_reopened_as_a_file` | `tests/unit/compose/script_draft_resume.py` |
| `test_data_in_a_boundaryless_multipart_part_is_refused_not_dropped` | `tests/unit/compose/script_draft_resume.py` |
| `test_files_over_the_cap_are_refused` | `tests/unit/compose/script_draft_resume.py` |
| `test_draft_whose_saves_would_be_too_large_is_refused` | `tests/unit/compose/script_draft_resume.py` |
| `test_bad_pk_unknown_and_not_a_draft` | `tests/unit/compose/script_draft_resume.py` |
| `test_resume_route_requires_csrf` | `tests/unit/compose/script_draft_resume.py` |
| `test_resumed_reply_sends_one_quote` | `tests/unit/compose/script_draft_resume.py` |
| `test_resumed_forward_sends_each_file_once` | `tests/unit/compose/script_draft_resume.py` |
| `test_send_never_fetches_the_original` | `tests/unit/compose/script_draft_resume.py` |
| `test_renumbered_original_still_gets_answered` | `tests/unit/compose/script_draft_resume.py` |
| `test_deleted_original_sends_threaded_without_answered` | `tests/unit/compose/script_draft_resume.py` |
| `test_changed_tail_sends_plain_text` | `tests/unit/compose/script_draft_resume.py` |
| `test_whole_resume_sends_as_typed` | `tests/unit/compose/script_draft_resume.py` |
| `test_resave_three_times_keeps_one_quote_and_the_bcc` | `tests/unit/compose/script_draft_resume.py` |
| `test_bad_resume_shape_is_400` | `tests/unit/compose/script_draft_resume.py` |
| `test_unflagged_reply_still_needs_its_original` | `tests/unit/compose/script_draft_resume.py` |
| `test_replaced_copy_is_refused_and_drafts_resynced` | `tests/unit/compose/script_draft_resume.py` |
| `test_a_draft_waiting_in_the_outbox_is_not_reopened` | `tests/unit/compose/script_draft_resume.py` |
| `test_a_copy_replaced_or_sent_while_edit_waited_is_refused` | `tests/unit/compose/script_draft_resume.py` |
| `test_save_and_send_cleanup_move_the_row_under_the_lock` | `tests/unit/compose/script_draft_resume.py` |
| `test_unreachable_server_opens_the_local_copy` | `tests/unit/compose/script_draft_resume.py` |
| `test_headers_that_wont_parse_fall_back_to_their_raw_values` | `tests/unit/compose/script_draft_resume.py` |
| `test_long_subject_reopens_as_drafted_however_often_it_is_saved` | `tests/unit/compose/script_draft_resume.py` |
| `test_comments_nested_past_any_parser_still_give_bare_addresses` | `tests/unit/compose/script_draft_resume.py` |
| `test_a_part_header_that_wont_parse_is_unreadable_not_a_500` | `tests/unit/compose/script_draft_resume.py` |
| `test_raw_utf8_headers_come_back_as_text_and_send_cleanly` | `tests/unit/compose/script_draft_resume.py` |
| `test_unreadable_draft_is_422_and_only_a_dead_server_502` | `tests/unit/compose/script_draft_resume.py` |
| `test_draft_live_reads_uidvalidity_and_uid` | `tests/unit/compose/script_draft_resume.py` |
| `test_draft_live_is_a_short_stateless_probe` | `tests/unit/compose/script_draft_resume.py` |
| `test_edit_probes_the_server_without_holding_the_ops_lock` | `tests/unit/compose/script_draft_resume.py` |

## `test_draft_resume_ui.py`: `tests/unit/compose/test_draft_resume_ui.py`

| Test | Now |
|---|---|
| `test_a_reopened_reply_opens_like_a_fresh_one` | `tests/unit/compose/test_draft_resume_ui.py` |
| `test_a_whole_draft_has_no_quote_to_fold` | `tests/unit/compose/test_draft_resume_ui.py` |
| `test_a_forward_keeps_its_section_and_no_thread` | `tests/unit/compose/test_draft_resume_ui.py` |
| `test_defaults_for_a_bare_answer` | `tests/unit/compose/test_draft_resume_ui.py` |
| `test_resume_fields_on_every_save_and_send` | `tests/unit/compose/test_draft_resume_ui.py` `test_resume_fields_on_every_save_and_send, test_resume_fields_of_no_draft_are_none` |
| `test_ai_draft_hidden_only_when_the_quote_is_in_the_box` | `tests/unit/compose/test_draft_resume_ui.py` |
| `test_the_signature_a_reopened_text_ends_with` | `tests/unit/compose/test_draft_resume_ui.py` |
| `test_notes_wording` | `tests/unit/compose/test_draft_resume_ui.py` |

## `test_expire.py`: `tests/unit/curation/test_expire.py`

| Test | Now |
|---|---|
| `test_domain_matches_exact_and_subdomain` | `tests/unit/curation/test_expire.py` |
| `test_domain_does_not_match_lookalike` | `tests/unit/curation/test_expire.py` |
| `test_sender_entry_wins_over_domain` | `tests/unit/curation/test_expire.py` |
| `test_no_rule_no_expiry` | `tests/unit/curation/test_expire.py` |
| `test_selects_only_older_than_limit` | `tests/unit/curation/test_expire.py` |
| `test_exactly_at_limit_is_kept` | `tests/unit/curation/test_expire.py` |
| `test_unknown_age_never_expires` | `tests/unit/curation/test_expire.py` |
| `test_arrival_prefers_internaldate` | `tests/unit/curation/test_expire.py` |
| `test_arrival_falls_back_to_date_header` | `tests/unit/curation/test_expire.py` |
| `test_arrival_unknown_is_none` | `tests/unit/curation/test_expire.py` |
| `test_expiry_survives_keep_block_edits` | `tests/unit/curation/test_expire.py` |
| `test_remove_leaves_expiry_alone` | `tests/unit/curation/test_expire.py` |
| `test_zero_hours_clears` | `tests/unit/curation/test_expire.py` |
| `test_legacy_file_without_expiry_loads` | `tests/unit/curation/test_expire.py` |
| `test_missing_file_loads_empty` | `tests/unit/curation/test_expire.py` |

## `test_hardening.py`: `tests/security/script_hardening.py`

| Test | Now |
|---|---|
| `test_starttls_off_this_machine_verifies_the_certificate` | `tests/security/script_hardening.py` |
| `test_starttls_on_loopback_accepts_bridges_own_certificate` | `tests/security/script_hardening.py` |
| `test_smtp_follows_the_same_loopback_rule` | `tests/security/script_hardening.py` |
| `test_a_move_without_MOVE_copies_then_expunges_only_its_uid` | `tests/security/script_hardening.py` |
| `test_a_failed_copy_leaves_the_message_untouched` | `tests/security/script_hardening.py` |
| `test_without_uidplus_the_message_stays_flagged_and_nothing_else_goes` | `tests/security/script_hardening.py` |
| `test_a_lost_connection_during_expunge_still_raises` | `tests/security/script_hardening.py` |
| `test_deleting_a_draft_copy_never_bulk_expunges` | `tests/security/script_hardening.py` |
| `test_replacing_a_draft_copy_never_bulk_expunges` | `tests/security/script_hardening.py` |
| `test_the_app_still_loads_on_its_own_host` | `tests/security/script_hardening.py` |
| `test_a_rebound_name_reads_nothing` | `tests/security/script_hardening.py` |
| `test_a_foreign_origin_reads_nothing` | `tests/security/script_hardening.py` |
| `test_a_refused_read_is_not_counted_as_someone_at_the_screen` | `tests/security/script_hardening.py` |
| `test_folder_names_from_list_pass_through_unchanged` | `tests/security/script_hardening.py` |
| `test_a_folder_name_that_would_break_the_command_is_refused` | `tests/security/script_hardening.py` |
| `test_a_move_to_an_injected_folder_sends_nothing` | `tests/security/script_hardening.py` |
| `test_an_injected_folder_in_the_url_reaches_no_imap_command` | `tests/security/script_hardening.py` |
| `test_a_read_another_site_starts_is_refused` | `tests/security/script_hardening.py`; `tests/security/test_perimeter.py` |
| `test_another_site_can_link_to_the_app_but_not_frame_it` | `tests/security/script_hardening.py` |
| `test_the_apps_own_reads_and_older_browsers_still_work` | `tests/security/script_hardening.py` `test_the_api_answers_only_the_pages_own_reads`; `tests/security/test_perimeter.py` `test_the_page_itself_opens_from_a_typed_url_or_an_older_browser` |
| `test_no_page_may_frame_the_app` | `tests/security/script_hardening.py` |
| `test_a_request_can_only_star_or_mark_read` | `tests/security/script_hardening.py` |
| `test_a_refused_move_keeps_the_message_and_says_so` | `tests/security/script_hardening.py` |
| `test_a_refused_gmail_archive_keeps_the_message` | `tests/security/script_hardening.py` |
| `test_an_accepted_move_still_files_the_message` | `tests/security/script_hardening.py` |
| `test_the_route_reports_a_refusal_and_offers_no_undo` | `tests/security/script_hardening.py` |
| `test_bcc_never_leaves_even_under_python_O` | `tests/security/script_hardening.py` |
| `test_a_malformed_config_is_never_written_even_under_python_O` | `tests/security/script_hardening.py` |
| `test_a_failed_read_answers_without_the_exception_text` | `tests/security/script_hardening.py` |
| `test_undo_ingest_leaves_the_inbox_watermark_alone` | `tests/security/script_hardening.py` |
| `test_undo_ingest_into_an_unopened_folder_keeps_its_first_full_sync` | `tests/security/script_hardening.py` |
| `test_cleanup_and_block_trash_mail_under_the_imap_lock` | `tests/security/script_hardening.py` |
| `test_nosync_reads_one_way_and_fails_toward_read_only` | `tests/security/script_hardening.py` |
| `test_trashing_the_same_message_offline_again_queues_it_again` | `tests/security/script_hardening.py` |
| `test_a_resubmit_with_the_same_mutation_id_queues_once` | `tests/security/script_hardening.py` |
| `test_two_cleanups_of_one_sender_undo_separately` | `tests/security/script_hardening.py` |
| `test_undo_groups_differ_across_page_loads` | `tests/security/script_hardening.py` |

## `test_send_after.py`: `tests/unit/compose/test_send_after.py`

| Test | Now |
|---|---|
| `test_passed_time_is_refused_before_building` | `tests/unit/compose/test_send_after.py` `test_a_bad_time_is_refused_before_building[passed]` |
| `test_more_than_a_year_out_is_refused` | `tests/unit/compose/test_send_after.py` `test_a_bad_time_is_refused_before_building[more_than_a_year_out]` |
| `test_malformed_time_is_refused` | `tests/unit/compose/test_send_after.py` `test_a_bad_time_is_refused_before_building[malformed]` |
| `test_future_time_is_scheduled` | `tests/unit/compose/test_send_after.py` |
| `test_no_time_sends_now` | `tests/unit/compose/test_send_after.py` |

## `test_send_guard.py`: `tests/unit/compose/test_send_guard.py`

| Test | Now |
|---|---|
| `test_strong_words_fire_in_every_mode` | `tests/unit/compose/test_send_guard.py` |
| `test_weak_words_fire_only_where_allowed` | `tests/unit/compose/test_send_guard.py` |
| `test_lookalikes_links_and_addresses_never_fire` | `tests/unit/compose/test_send_guard.py` |
| `test_a_reply_about_their_file_never_warns` | `tests/unit/compose/test_send_guard.py` |
| `test_long_unbroken_text_checks_fast` | `tests/unit/compose/test_send_guard.py` |
| `test_the_word_is_echoed_as_typed` | `tests/unit/compose/test_send_guard.py` |
| `test_typed_text_leaves_out_signature_and_quote` | `tests/unit/compose/test_send_guard.py` `test_typed_text_leaves_out_signature_and_quote, test_typed_text_leaves_out_signature_and_quote_and_their_blank_lines` |
| `test_text_on_either_side_of_your_signature_is_yours` | `tests/unit/compose/test_send_guard.py` `test_text_on_either_side_of_your_signature_is_yours, test_text_below_your_signature_is_yours` |
| `test_empty_subject` | `tests/unit/compose/test_send_guard.py` |
| `test_an_inherited_subject_is_not_your_wording` | `tests/unit/compose/test_send_guard.py` |
| `test_a_file_still_reading_is_the_only_line` | `tests/unit/compose/test_send_guard.py` |
| `test_a_passed_send_time` | `tests/unit/compose/test_send_guard.py` |
| `test_what_counts_as_attached` | `tests/unit/compose/test_send_guard.py` |
| `test_reply_all_size` | `tests/unit/compose/test_send_guard.py` |
| `test_a_wave_through_covers_up_to_that_many_people` | `tests/unit/compose/test_send_guard.py` |
| `test_removable_counts_only_people_still_copied_in` | `tests/unit/compose/test_send_guard.py` |
| `test_new_to_you_never_stops_a_send_alone` | `tests/unit/compose/test_send_guard.py` |
| `test_names_list` | `tests/unit/compose/test_send_guard.py` |
| `test_exact_wording_of_every_line` | `tests/unit/compose/test_send_guard.py` |

## `test_thread_file.py`

| Test | Now |
|---|---|
| `test_files_every_message_of_the_thread_in_the_anchors_folder` | `tests/unit/mailbox/test_thread_filing.py` |
| `test_any_message_can_anchor_the_conversation` | `tests/unit/mailbox/test_thread_filing.py` |
| `test_message_without_a_thread_is_a_conversation_of_one` | `tests/unit/mailbox/test_thread_filing.py` `test_a_message_without_a_thread_is_a_conversation_of_one` |
| `test_trash_route_uses_trash` | `tests/unit/mailbox/test_thread_filing.py` `test_the_trash_route_uses_trash` |
| `test_one_undo_entry_for_the_whole_conversation` | `tests/unit/mailbox/test_thread_filing.py` |
| `test_a_bulk_selection_shares_the_callers_undo_group` | `tests/unit/mailbox/test_thread_filing.py` |
| `test_partial_failure_reports_what_was_filed` | `tests/unit/mailbox/test_thread_filing.py` `test_a_partial_failure_reports_what_was_filed` |
| `test_total_failure_is_not_ok` | `tests/unit/mailbox/test_thread_filing.py` `test_a_total_failure_is_not_ok_and_leaves_nothing_to_undo` |
| `test_unreachable_imap_queues_each_message` | `tests/unit/mailbox/test_thread_filing.py` `test_an_unreachable_server_queues_each_message` |
| `test_missing_or_bad_anchor` | `tests/unit/mailbox/test_thread_filing.py` `test_a_missing_or_bad_anchor` |

## `test_upcoming.py`: `tests/unit/calendar/script_upcoming.py`

| Test | Now |
|---|---|
| `test_lists_future_events_soonest_first_with_local_times` | `tests/unit/calendar/script_upcoming.py` |
| `test_an_event_that_has_ended_drops_out_but_one_in_progress_stays` | `tests/unit/calendar/script_upcoming.py` |
| `test_utc_times_come_back_as_local_wall_clock` | `tests/unit/calendar/script_upcoming.py` |
| `test_an_all_day_event_today_stays_until_midnight` | `tests/unit/calendar/script_upcoming.py` |
| `test_an_update_replaces_the_original` | `tests/unit/calendar/script_upcoming.py` |
| `test_a_cancellation_removes_the_event` | `tests/unit/calendar/script_upcoming.py` |
| `test_a_series_is_listed_at_its_next_occurrence` | `tests/unit/calendar/script_upcoming.py` |
| `test_moving_or_cancelling_one_occurrence_leaves_the_series` | `tests/unit/calendar/script_upcoming.py` |
| `test_cancelling_the_series_drops_its_moved_occurrences_too` | `tests/unit/calendar/script_upcoming.py` |
| `test_a_rule_it_cannot_expand_shows_its_first_date_while_ahead` | `tests/unit/calendar/script_upcoming.py` |
| `test_trash_junk_unfetched_and_old_mail_are_not_read` | `tests/unit/calendar/script_upcoming.py` |
| `test_account_filter_and_limit` | `tests/unit/calendar/script_upcoming.py` |
| `test_a_broken_calendar_part_is_skipped` | `tests/unit/calendar/script_upcoming.py` |
| `test_a_weekly_event_keeps_its_wall_clock_time_across_daylight_saving` | `tests/unit/calendar/script_upcoming.py` |
| `test_count_until_and_exdate_end_or_skip_occurrences` | `tests/unit/calendar/script_upcoming.py` |
| `test_daily_monthly_and_yearly_rules` | `tests/unit/calendar/script_upcoming.py` |
| `test_an_occurrence_in_progress_is_still_next` | `tests/unit/calendar/script_upcoming.py` |
| `test_an_all_day_series_and_a_floating_one` | `tests/unit/calendar/script_upcoming.py` |
| `test_a_rule_it_cannot_read_gives_none_not_a_wrong_date` | `tests/unit/calendar/script_upcoming.py` |
| `test_a_one_off_event_is_its_own_only_occurrence` | `tests/unit/calendar/script_upcoming.py` |
| `test_ingest_keeps_an_invite_so_it_is_listed_unopened` | `tests/unit/calendar/script_upcoming.py` |
| `test_an_inline_only_invite_is_recorded_once_and_listed` | `tests/unit/calendar/script_upcoming.py` |
| `test_waiting_names_who_a_sent_thread_waits_on` | `tests/unit/calendar/script_upcoming.py` |

## `tests.py`: `tests/unit/curation/test_classifier.py`

A linear script, now tables: `test_classify`, `test_is_junk` and `test_a_non_ascii_subject_does_not_crash`.

## `tests_sanitize.py`: `tests/security/test_sanitize.py`

A linear script, now tables: `test_nothing_dangerous_survives`, `test_safe_content_is_kept`, `test_a_raster_data_image_is_kept_when_images_load` and `test_junk_does_not_raise`.
